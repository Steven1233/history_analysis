import os
import sys
import argparse
import csv
import multiprocessing
from collections import OrderedDict
import numpy as np
import netCDF4 as nc


def collect_nc_files(root_dir):
    files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith('.nc'):
                files.append(os.path.join(dirpath, f))
    files.sort()
    return files


def extract_date_from_filename(filepath):
    basename = os.path.basename(filepath)
    parts = basename.replace('.nc', '').split('_')
    if len(parts) >= 5:
        yyyy, mm, dd, hh = parts[1], parts[2], parts[3], parts[4]
        return f"{yyyy}-{mm}-{dd} {hh}:00"
    return "unknown"


def extract_baseline(filepath):
    dataset = nc.Dataset(filepath, 'r')
    baseline = OrderedDict()
    baseline['dimensions'] = OrderedDict()
    for dim_name, dim in dataset.dimensions.items():
        baseline['dimensions'][dim_name] = len(dim)
    baseline['variables'] = OrderedDict()
    for var_name, var in dataset.variables.items():
        baseline['variables'][var_name] = {
            'dtype': str(var.dtype),
            'shape': var.shape,
            'dims': var.dimensions,
        }
    baseline['global_attrs'] = set(dataset.ncattrs())
    dataset.close()
    return baseline


def check_single_file(args):
    filepath, baseline = args
    date_str = extract_date_from_filename(filepath)
    structure_issues = []
    data_issues = []

    try:
        dataset = nc.Dataset(filepath, 'r')

        if set(dataset.dimensions.keys()) != set(baseline['dimensions'].keys()):
            structure_issues.append(
                f"dimension names differ: got {sorted(dataset.dimensions.keys())}, "
                f"expected {sorted(baseline['dimensions'].keys())}"
            )
        else:
            for dim_name, expected_size in baseline['dimensions'].items():
                actual_size = len(dataset.dimensions[dim_name])
                if actual_size != expected_size:
                    structure_issues.append(
                        f"dimension '{dim_name}' size differs: got {actual_size}, expected {expected_size}"
                    )

        if set(dataset.variables.keys()) != set(baseline['variables'].keys()):
            structure_issues.append(
                f"variable names differ: got {sorted(dataset.variables.keys())}, "
                f"expected {sorted(baseline['variables'].keys())}"
            )
        else:
            for var_name, expected_info in baseline['variables'].items():
                var = dataset.variables[var_name]
                if str(var.dtype) != expected_info['dtype']:
                    structure_issues.append(
                        f"variable '{var_name}' dtype differs: got {var.dtype}, expected {expected_info['dtype']}"
                    )
                if var.shape != expected_info['shape']:
                    structure_issues.append(
                        f"variable '{var_name}' shape differs: got {var.shape}, expected {expected_info['shape']}"
                    )
                if var.dimensions != expected_info['dims']:
                    structure_issues.append(
                        f"variable '{var_name}' dims differ: got {var.dimensions}, expected {expected_info['dims']}"
                    )

        actual_attrs = set(dataset.ncattrs())
        if actual_attrs != baseline['global_attrs']:
            structure_issues.append(
                f"global attributes differ: got {sorted(actual_attrs)}, expected {sorted(baseline['global_attrs'])}"
            )

        if 'rain_rate' in dataset.variables:
            rain_rate = dataset.variables['rain_rate']

            data = rain_rate[:]

            if hasattr(data, 'mask'):
                if data.mask.all():
                    data_issues.append("rain_rate is fully masked")
                    dataset.close()
                    return {
                        'filepath': filepath,
                        'date': date_str,
                        'structure_issues': structure_issues,
                        'data_issues': data_issues,
                    }
                fill_mask = data.mask
                valid_data = data.data[~fill_mask]
            else:
                valid_data = np.array(data).flatten()
                fill_mask = None

            if isinstance(valid_data, np.ma.MaskedArray):
                valid_data = valid_data.compressed()

            if valid_data.size == 0:
                data_issues.append("rain_rate has no valid data")
            else:
                if np.all(np.isnan(valid_data)):
                    data_issues.append("rain_rate is all NaN")
                elif np.all(valid_data == 0):
                    data_issues.append("rain_rate is all zero")

                if np.any(np.isinf(valid_data)):
                    data_issues.append("rain_rate contains inf values")

                if np.any(valid_data < 0):
                    data_issues.append("rain_rate contains negative values")

            if hasattr(rain_rate, '_FillValue') and rain_rate._FillValue is not None:
                fill_val = rain_rate._FillValue
                if fill_mask is not None:
                    raw_data = data.data
                    if np.all(raw_data == fill_val):
                        data_issues.append("rain_rate is all fill_value")
                else:
                    if np.all(np.array(data) == fill_val):
                        data_issues.append("rain_rate is all fill_value")

        dataset.close()

    except Exception as e:
        structure_issues.append(f"failed to read file: {e}")

    return {
        'filepath': filepath,
        'date': date_str,
        'structure_issues': structure_issues,
        'data_issues': data_issues,
    }


def main():
    parser = argparse.ArgumentParser(description='Check GMCP NetCDF data consistency')
    parser.add_argument('--path', default='G:/GMCP', help='Root directory of GMCP data')
    parser.add_argument('--workers', type=int, default=os.cpu_count(), help='Number of parallel workers')
    parser.add_argument('--output', default=None, help='Output CSV file path')
    parser.add_argument('--verbose', action='store_true', help='Show verbose progress')
    args = parser.parse_args()

    nc_files = collect_nc_files(args.path)
    if not nc_files:
        print(f"No .nc files found in {args.path}")
        sys.exit(1)

    print(f"Found {len(nc_files)} .nc files")

    baseline = extract_baseline(nc_files[0])
    if args.verbose:
        print(f"Baseline extracted from: {nc_files[0]}")
        print(f"  Dimensions: {dict(baseline['dimensions'])}")
        print(f"  Variables: {list(baseline['variables'].keys())}")
        print(f"  Global attributes: {sorted(baseline['global_attrs'])}")

    task_args = [(f, baseline) for f in nc_files]

    with multiprocessing.Pool(processes=args.workers) as pool:
        results = []
        for i, result in enumerate(pool.imap_unordered(check_single_file, task_args), 1):
            results.append(result)
            if args.verbose and i % 500 == 0:
                print(f"  Processed {i}/{len(nc_files)} files...")

    results.sort(key=lambda r: r['date'])

    normal_count = 0
    structure_issue_files = []
    data_issue_files = []

    for r in results:
        has_structure = len(r['structure_issues']) > 0
        has_data = len(r['data_issues']) > 0
        if not has_structure and not has_data:
            normal_count += 1
        if has_structure:
            structure_issue_files.append(r)
        if has_data:
            data_issue_files.append(r)

    abnormal_count = len(nc_files) - normal_count

    print(f"\n{'='*60}")
    print(f"Scan complete")
    print(f"  Total files:   {len(nc_files)}")
    print(f"  Normal files:  {normal_count}")
    print(f"  Abnormal files: {abnormal_count}")
    print(f"{'='*60}")

    if structure_issue_files:
        print(f"\nStructure issues ({len(structure_issue_files)} files):")
        for r in structure_issue_files:
            for issue in r['structure_issues']:
                print(f"  [{r['date']}] {issue}")

    if data_issue_files:
        print(f"\nData issues ({len(data_issue_files)} files):")
        for r in data_issue_files:
            for issue in r['data_issues']:
                print(f"  [{r['date']}] {issue}")

    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['filepath', 'date', 'issue_type', 'issue_detail'])
            for r in results:
                for issue in r['structure_issues']:
                    writer.writerow([r['filepath'], r['date'], 'structure', issue])
                for issue in r['data_issues']:
                    writer.writerow([r['filepath'], r['date'], 'data', issue])
        print(f"\nResults written to {args.output}")


if __name__ == '__main__':
    main()
