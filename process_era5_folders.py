#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
处理ERA5数据文件夹，提取指定条件的数据并保存为多种格式

提取条件：
- 区域范围：东经104-124，北纬15-30
- 高度层：925hPa和500hPa
- 时间：00UTC, 06UTC, 12UTC, 18UTC

输出格式：
1. NC格式：保存为原始数据格式
2. Python格式：转换为numpy和pickle格式，便于Python调用

文件命名规则：
- 文件夹名称：对应处理的nc文件夹名称，增加_extracted后缀
- 文件名：包含原始文件名、高度层信息和UTC时间信息
"""

import numpy as np
from netCDF4 import Dataset, num2date
import os
import glob
import pickle


def extract_and_save_data(nc_file, output_root_dir):
    """
    提取单个NC文件的数据，并按照要求保存
    
    参数:
        nc_file (str): 输入NC文件路径
        output_root_dir (str): 输出根目录
    """
    print(f"\n处理文件: {os.path.basename(nc_file)}")
    print("-" * 80)
    
    # 1. 获取文件名信息
    base_name = os.path.splitext(os.path.basename(nc_file))[0]
    
    # 2. 创建输出文件夹
    # 输出根目录下创建同名文件夹，不增加_extracted
    output_nc_dir = os.path.join(output_root_dir, base_name)  # NC文件输出目录
    output_py_dir = os.path.join(output_root_dir, f"{base_name}_py")  # Python格式输出目录
    
    os.makedirs(output_nc_dir, exist_ok=True)
    os.makedirs(output_py_dir, exist_ok=True)
    
    try:
        # 3. 打开NC文件
        with Dataset(nc_file, 'r') as src:
            # 4. 获取维度数据
            # 打印所有变量名，方便调试
            print(f"  所有变量: {list(src.variables.keys())}")
            
            # 尝试获取时间变量，支持多种可能的变量名
            time_var_name = None
            time_var_names = ['valid_time', 'time']
            for tvn in time_var_names:
                if tvn in src.variables:
                    time_var_name = tvn
                    break
            
            if not time_var_name:
                print(f"  错误：未找到时间变量，尝试的变量名: {time_var_names}")
                return False
            
            print(f"  使用时间变量: {time_var_name}")
            
            # 尝试获取气压层变量，支持多种可能的变量名
            pressure_var_name = None
            pressure_var_names = ['pressure_level', 'level', 'isobaricInhPa', 'isobaric']
            for pvn in pressure_var_names:
                if pvn in src.variables:
                    pressure_var_name = pvn
                    break
            
            if not pressure_var_name:
                print(f"  错误：未找到气压层变量，尝试的变量名: {pressure_var_names}")
                return False
            
            print(f"  使用气压层变量: {pressure_var_name}")
            
            # 获取数据
            try:
                valid_time = src.variables[time_var_name][:]
                pressure_level = src.variables[pressure_var_name][:]
                latitude = src.variables['latitude'][:]
                longitude = src.variables['longitude'][:]
            except KeyError as e:
                print(f"  错误：缺少变量 {e}")
                return False
            
            # 5. 转换时间戳为datetime对象
            time_units = src.variables[time_var_name].units
            time_calendar = getattr(src.variables[time_var_name], 'calendar', 'standard')
            datetime_array = num2date(valid_time, units=time_units, calendar=time_calendar)
            
            # 6. 筛选条件
            # 时间筛选：00UTC, 06UTC, 12UTC, 18UTC
            time_info = []
            time_indices = []
            for i, dt in enumerate(datetime_array):
                if dt.hour in [0, 6, 12, 18]:
                    time_indices.append(i)
                    time_info.append(dt.strftime('%HUTC'))
            
            if not time_indices:
                print(f"  警告：未找到符合条件的时间点，跳过该文件")
                return False
            
            print(f"  时间点: {time_info}")
            
            # 气压层筛选：925hPa和500hPa
            pressure_info = []
            pressure_indices = []
            for i, pl in enumerate(pressure_level):
                if pl in [925.0, 500.0]:
                    pressure_indices.append(i)
                    pressure_info.append(f"{int(pl)}hPa")
            
            if len(pressure_indices) < 2:
                print(f"  警告：只找到{len(pressure_indices)}个符合条件的气压层，跳过该文件")
                return False
            
            print(f"  气压层: {pressure_info}")
            
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
            
            print(f"  区域范围: 东经104-124, 北纬15-30")
            print(f"  数据网格: {len(lat_indices)}×{len(lon_indices)}")
            
            # 7. 读取变量数据
            variable_names = []
            for var_name in src.variables:
                if var_name not in ['number', time_var_name, pressure_var_name, 'latitude', 'longitude', 'expver']:
                    variable_names.append(var_name)
            
            print(f"  变量列表: {variable_names}")
            
            # 8. 提取并保存数据
            for var_name in variable_names:
                # 读取原始数据
                data = src.variables[var_name][time_indices, :, :, :]
                data = data[:, pressure_indices, :, :]
                data = data[:, :, lat_indices, :]
                data = data[:, :, :, lon_indices]
                
                # 将MaskedArray转换为普通numpy数组
                if hasattr(data, 'filled'):
                    data = data.filled(np.nan)
                
                # 9. 保存为NC格式
                for time_idx in range(len(time_indices)):
                    for pressure_idx in range(len(pressure_indices)):
                        # 生成文件名
                        time_str = time_info[time_idx]
                        pressure_str = pressure_info[pressure_idx]
                        
                        nc_output_file = os.path.join(output_nc_dir, 
                                                   f"{base_name}_{pressure_str}_{time_str}.nc")
                        
                        # 创建输出NC文件
                        with Dataset(nc_output_file, 'w', format='NETCDF4') as dst:
                            # 创建维度
                            dst.createDimension('latitude', len(lat_indices))
                            dst.createDimension('longitude', len(lon_indices))
                            
                            # 创建变量
                            # 纬度变量
                            lat_var = dst.createVariable('latitude', float64, ('latitude',))
                            lat_var.units = 'degrees_north'
                            lat_var.long_name = 'latitude'
                            lat_var[:] = latitude[lat_indices]
                            
                            # 经度变量
                            lon_var = dst.createVariable('longitude', float64, ('longitude',))
                            lon_var.units = 'degrees_east'
                            lon_var.long_name = 'longitude'
                            lon_var[:] = longitude[lon_indices]
                            
                            # 数据变量
                            dst_var = dst.createVariable(var_name, data.dtype, ('latitude', 'longitude'))
                            
                            # 复制变量属性
                            for attr_name in src.variables[var_name].ncattrs():
                                if attr_name != '_FillValue':
                                    setattr(dst_var, attr_name, getattr(src.variables[var_name], attr_name))
                            
                            # 写入数据切片
                            dst_var[:] = data[time_idx, pressure_idx, :, :]
                            
                        print(f"  保存NC: {os.path.basename(nc_output_file)}")
                
                # 10. 保存为Python格式
                # 准备保存的数据结构
                save_dict = {
                    'variable_name': var_name,
                    'variable_units': getattr(src.variables[var_name], 'units', 'unknown'),
                    'valid_time': valid_time[time_indices],
                    'valid_time_datetime': datetime_array[time_indices],
                    'pressure_level': pressure_level[pressure_indices],
                    'latitude': latitude[lat_indices],
                    'longitude': longitude[lon_indices],
                    'data': data,
                    'metadata': {
                        'original_file': base_name,
                        'extraction_time': os.popen('date /t').read().strip() + ' ' + os.popen('time /t').read().strip(),
                        'extraction_area': '东经104-124, 北纬15-30',
                        'time_points': time_info,
                        'pressure_levels': pressure_info
                    }
                }
                
                # 保存为pickle格式
                pkl_output_file = os.path.join(output_py_dir, f"{base_name}_{var_name}.pkl")
                with open(pkl_output_file, 'wb') as f:
                    pickle.dump(save_dict, f)
                
                print(f"  保存PKL: {os.path.basename(pkl_output_file)}")
                
                # 保存为numpy格式
                npy_output_file = os.path.join(output_py_dir, f"{base_name}_{var_name}.npy")
                np.save(npy_output_file, data)
                
                print(f"  保存NPY: {os.path.basename(npy_output_file)}")
                
                # 11. 生成字段说明文档
                generate_field_description(save_dict, output_py_dir, base_name, var_name)
        
        print(f"\n  处理完成")
        print(f"  NC文件保存到: {output_nc_dir}")
        print(f"  Python格式保存到: {output_py_dir}")
        return True
    except Exception as e:
        print(f"  处理文件时出错: {e}")
        return False


def generate_field_description(save_dict, output_dir, base_name, var_name):
    """
    生成字段说明文档
    
    参数:
        save_dict (dict): 保存的数据字典
        output_dir (str): 输出目录
        base_name (str): 基础文件名
        var_name (str): 变量名
    """
    desc_file = os.path.join(output_dir, f"{base_name}_{var_name}_field_description.txt")
    
    with open(desc_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write(f"ERA5数据字段说明 - {var_name}\n")
        f.write("=" * 80 + "\n\n")
        
        # 基本信息
        metadata = save_dict['metadata']
        f.write(f"原始文件: {metadata['original_file']}\n")
        f.write(f"生成时间: {metadata['extraction_time']}\n")
        f.write(f"提取区域: {metadata['extraction_area']}\n")
        f.write(f"时间点: {metadata['time_points']}\n")
        f.write(f"气压层: {metadata['pressure_levels']}\n\n")
        
        # 数据结构
        f.write("数据结构\n")
        f.write("-" * 60 + "\n")
        f.write(f"变量名称: {save_dict['variable_name']}\n")
        f.write(f"变量单位: {save_dict['variable_units']}\n")
        f.write(f"数据形状: {save_dict['data'].shape} (时间, 气压层, 纬度, 经度)\n")
        f.write(f"纬度范围: {save_dict['latitude'][0]:.2f}°N 到 {save_dict['latitude'][-1]:.2f}°N\n")
        f.write(f"经度范围: {save_dict['longitude'][0]:.2f}°E 到 {save_dict['longitude'][-1]:.2f}°E\n")
        f.write(f"数据类型: {save_dict['data'].dtype}\n\n")
        
        # 维度说明
        f.write("维度说明\n")
        f.write("-" * 60 + "\n")
        f.write(f"时间维度: {len(save_dict['valid_time'])} 个时间点\n")
        f.write(f"气压层维度: {len(save_dict['pressure_level'])} 个气压层\n")
        f.write(f"纬度维度: {len(save_dict['latitude'])} 个点\n")
        f.write(f"经度维度: {len(save_dict['longitude'])} 个点\n\n")
        
        # 数据示例
        f.write("数据示例\n")
        f.write("-" * 60 + "\n")
        data = save_dict['data']
        if data.size > 0:
            # 显示第一个时间点、第一个气压层的部分数据
            sample = data[0, 0, :2, :3]  # 前2行3列
            f.write(f"第一个时间点({metadata['time_points'][0]}), 第一个气压层({metadata['pressure_levels'][0]})的数据示例:\n")
            for row in sample:
                f.write(f"  {row}\n")
        
        # 使用说明
        f.write("\n" + "=" * 80 + "\n")
        f.write("使用说明\n")
        f.write("-" * 60 + "\n")
        f.write("\n1. 读取pickle文件\n")
        f.write("import pickle\n")
        f.write(f"with open('{base_name}_{var_name}.pkl', 'rb') as f:\n")
        f.write("    data_dict = pickle.load(f)\n")
        f.write("\n2. 访问数据\n")
        f.write(f"data = data_dict['data']          # 数据数组\n")
        f.write(f"lat = data_dict['latitude']       # 纬度\n")
        f.write(f"lon = data_dict['longitude']      # 经度\n")
        f.write(f"time = data_dict['valid_time_datetime']  # 时间\n")
        f.write(f"pressure = data_dict['pressure_level']  # 气压层\n")
        f.write("\n3. 数据访问示例\n")
        f.write(f"# 获取第一个时间点，第一个气压层的数据\n")
        f.write(f"data_slice = data[0, 0, :, :]\n")
        f.write(f"# 获取特定经纬度的数据\n")
        f.write(f"# 查找纬度30.0°N和经度104.0°E的索引\n")
        f.write(f"lat_idx = np.where(lat == 30.0)[0][0]\n")
        f.write(f"lon_idx = np.where(lon == 104.0)[0][0]\n")
        f.write(f"point_data = data[:, :, lat_idx, lon_idx]\n")
        
    print(f"  生成说明: {os.path.basename(desc_file)}")


def process_folder(input_folder, output_root_dir):
    """
    处理指定文件夹中的所有NC文件
    
    参数:
        input_folder (str): 输入文件夹路径
        output_root_dir (str): 输出根目录
    """
    print("=" * 80)
    print(f"处理ERA5数据文件夹")
    print(f"输入文件夹: {input_folder}")
    print(f"输出根目录: {output_root_dir}")
    print("=" * 80)
    
    # 获取文件夹名称
    folder_name = os.path.basename(input_folder)
    
    # 创建输出根目录下的子目录，增加_extracted后缀
    output_subdir = os.path.join(output_root_dir, f"{folder_name}_extracted")
    os.makedirs(output_subdir, exist_ok=True)
    
    # 获取所有nc文件
    nc_files = glob.glob(os.path.join(input_folder, "*.nc"))
    
    if not nc_files:
        print(f"警告：在 {input_folder} 中未找到nc文件")
        return 0
    
    print(f"找到 {len(nc_files)} 个nc文件")
    
    # 处理每个文件
    success_count = 0
    for nc_file in nc_files:
        try:
            if extract_and_save_data(nc_file, output_subdir):
                success_count += 1
        except Exception as e:
            print(f"处理文件 {nc_file} 时出错: {e}")
    
    print("\n" + "=" * 80)
    print(f"文件夹处理完成")
    print(f"成功处理: {success_count}/{len(nc_files)} 个文件")
    print(f"输出结果保存在: {output_subdir}")
    print("=" * 80)
    
    return success_count


def main():
    """
    主函数，处理所有指定的文件夹
    """
    print("=" * 80)
    print("ERA5数据文件夹处理脚本")
    print("=" * 80)
    
    # 指定需要处理的文件夹列表
    input_folders = [
        r'H:\relative_humidity_2000_2024',
        r'H:\u_winds_2000_2024',
        r'H:\v_winds_2000_2024'
    ]
    
    # 输出根目录
    output_root_dir = r'H:'
    
    # 处理每个文件夹
    total_success = 0
    total_files = 0
    
    for input_folder in input_folders:
        # 检查文件夹是否存在
        if not os.path.exists(input_folder):
            print(f"警告：文件夹不存在 {input_folder}")
            continue
        
        # 获取文件夹中的文件数量
        nc_files = glob.glob(os.path.join(input_folder, "*.nc"))
        total_files += len(nc_files)
        
        # 处理文件夹
        success = process_folder(input_folder, output_root_dir)
        total_success += success
    
    print("\n" + "=" * 80)
    print("所有文件夹处理完成")
    print(f"总成功处理: {total_success}/{total_files} 个文件")
    print(f"输出结果保存在: {output_root_dir}")
    print("=" * 80)


if __name__ == "__main__":
    # 添加float64类型定义
    float64 = np.float64
    main()