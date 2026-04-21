#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量提取文件夹中NetCDF文件的指定数据

提取条件：
- 区域范围：东经104-124，北纬15-30
- 高度层：925hPa和500hPa
- 时间：00UTC, 06UTC, 12UTC, 18UTC
"""

import numpy as np
from netCDF4 import Dataset, num2date
import os
import glob


def extract_nc_data(input_file, output_file):
    """
    提取单个NetCDF文件中指定条件的数据
    
    参数:
        input_file (str): 输入NetCDF文件路径
        output_file (str): 输出NetCDF文件路径
    """
    print(f"\n处理文件: {os.path.basename(input_file)}")
    print("-" * 60)
    
    # 打开输入NetCDF文件
    with Dataset(input_file, 'r') as src:
        # 1. 获取维度数据
        valid_time = src.variables['valid_time'][:]
        pressure_level = src.variables['pressure_level'][:]
        latitude = src.variables['latitude'][:]
        longitude = src.variables['longitude'][:]
        
        # 2. 筛选条件
        # 时间筛选：00UTC, 06UTC, 12UTC, 18UTC
        time_units = src.variables['valid_time'].units
        time_calendar = src.variables['valid_time'].calendar
        datetime_array = num2date(valid_time, units=time_units, calendar=time_calendar)
        
        time_indices = []
        for i, dt in enumerate(datetime_array):
            if dt.hour in [0, 6, 12, 18]:
                time_indices.append(i)
        
        if not time_indices:
            print("  警告：未找到符合条件的时间点，跳过该文件")
            return False
        
        print(f"  时间点: {[datetime_array[i].strftime('%H:%M UTC') for i in time_indices]}")
        
        # 气压层筛选：925hPa和500hPa
        pressure_indices = []
        for i, pl in enumerate(pressure_level):
            if pl in [925.0, 500.0]:
                pressure_indices.append(i)
        
        if len(pressure_indices) < 2:
            print(f"  警告：只找到{len(pressure_indices)}个符合条件的气压层，跳过该文件")
            return False
        
        # 纬度筛选：北纬15-30
        lat_indices = np.where((latitude >= 15.0) & (latitude <= 30.0))[0]
        if not lat_indices.size:
            print("  警告：未找到符合条件的纬度范围，跳过该文件")
            return False
        
        # 经度筛选：东经104-124
        lon_indices = np.where((longitude >= 104.0) & (longitude <= 124.0))[0]
        if not lon_indices.size:
            print("  警告：未找到符合条件的经度范围，跳过该文件")
            return False
        
        # 3. 创建输出NetCDF文件
        with Dataset(output_file, 'w', format='NETCDF4') as dst:
            # 4. 复制全局属性
            for attr_name in src.ncattrs():
                setattr(dst, attr_name, getattr(src, attr_name))
            
            # 5. 创建维度
            dst.createDimension('valid_time', len(time_indices))
            dst.createDimension('pressure_level', len(pressure_indices))
            dst.createDimension('latitude', len(lat_indices))
            dst.createDimension('longitude', len(lon_indices))
            
            if 'number' in src.dimensions:
                dst.createDimension('number', src.dimensions['number'].size)
            if 'expver' in src.dimensions:
                dst.createDimension('expver', src.dimensions['expver'].size if not src.dimensions['expver'].isunlimited() else None)
            
            # 6. 通用函数：创建变量并复制属性
            def create_and_copy_var(var_name, dims):
                src_var = src.variables[var_name]
                fill_value = getattr(src_var, '_FillValue', None)
                dst_var = dst.createVariable(var_name, src_var.dtype, dims, fill_value=fill_value)
                for attr_name in src_var.ncattrs():
                    if attr_name != '_FillValue':
                        setattr(dst_var, attr_name, getattr(src_var, attr_name))
                return dst_var
            
            # 7. 创建变量
            # 时间变量
            time_var = create_and_copy_var('valid_time', ('valid_time',))
            time_var[:] = valid_time[time_indices]
            
            # 气压层变量
            pressure_var = create_and_copy_var('pressure_level', ('pressure_level',))
            pressure_var[:] = pressure_level[pressure_indices]
            
            # 纬度变量
            lat_var = create_and_copy_var('latitude', ('latitude',))
            lat_var[:] = latitude[lat_indices]
            
            # 经度变量
            lon_var = create_and_copy_var('longitude', ('longitude',))
            lon_var[:] = longitude[lon_indices]
            
            # number变量
            if 'number' in src.variables:
                number_var = create_and_copy_var('number', src.variables['number'].dimensions)
                number_var[:] = src.variables['number'][:]
            
            # expver变量
            if 'expver' in src.variables:
                expver_var = create_and_copy_var('expver', ('valid_time',))
                expver_var[:] = src.variables['expver'][time_indices]
            
            # 8. 提取风场数据
            extracted_vars = []
            for var_name in ['u', 'v']:
                if var_name in src.variables:
                    # 创建目标变量
                    dst_var = create_and_copy_var(var_name, ('valid_time', 'pressure_level', 'latitude', 'longitude'))
                    
                    # 提取数据
                    data = src.variables[var_name][time_indices, :, :, :]
                    data = data[:, pressure_indices, :, :]
                    data = data[:, :, lat_indices, :]
                    data = data[:, :, :, lon_indices]
                    
                    # 写入数据
                    dst_var[:] = data
                    
                    extracted_vars.append(var_name)
                    print(f"  提取变量 {var_name}: 形状 {data.shape}")
        
    print(f"  保存到: {os.path.basename(output_file)}")
    return True


def batch_extract(input_dir, output_dir):
    """
    批量处理文件夹中的NetCDF文件
    
    参数:
        input_dir (str): 输入文件夹路径
        output_dir (str): 输出文件夹路径
    """
    print("=" * 80)
    print(f"批量提取NetCDF数据")
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print("=" * 80)
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 获取所有nc文件
    nc_files = glob.glob(os.path.join(input_dir, "*.nc"))
    
    if not nc_files:
        print(f"错误: 在 {input_dir} 中未找到nc文件")
        return
    
    print(f"找到 {len(nc_files)} 个nc文件")
    
    # 处理每个文件
    success_count = 0
    for input_file in nc_files:
        # 生成输出文件名
        filename = os.path.basename(input_file)
        output_file = os.path.join(output_dir, f"extracted_{filename}")
        
        # 提取数据
        if extract_nc_data(input_file, output_file):
            success_count += 1
    
    print("\n" + "=" * 80)
    print(f"批量处理完成")
    print(f"成功处理: {success_count}/{len(nc_files)} 个文件")
    print(f"输出目录: {output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='批量提取NetCDF文件的指定数据')
    parser.add_argument('--input', '-i', type=str, default='.', 
                        help='输入文件夹路径，默认当前目录')
    parser.add_argument('--output', '-o', type=str, default='extracted_data', 
                        help='输出文件夹路径，默认创建extracted_data目录')
    
    args = parser.parse_args()
    
    # 调用批量提取函数
    batch_extract(args.input, args.output)