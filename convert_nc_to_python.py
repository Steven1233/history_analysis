#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
将抽取的NetCDF文件转换为Python方便调用的格式

支持的输出格式：
- numpy (.npy)：适合数值计算
- pickle (.pkl)：适合保存Python对象
- csv (.csv)：适合表格数据查看
"""

import numpy as np
import pickle
import csv
import os
from netCDF4 import Dataset, num2date
import glob


def convert_nc_to_python(nc_file, output_dir, format='npy'):
    """
    将单个NetCDF文件转换为Python格式
    
    参数:
        nc_file (str): 输入NetCDF文件路径
        output_dir (str): 输出文件夹路径
        format (str): 输出格式，可选 'npy', 'pkl', 'csv'
    
    返回:
        dict: 转换结果信息
    """
    print(f"\n处理文件: {os.path.basename(nc_file)}")
    print("-" * 60)
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 打开NetCDF文件
    with Dataset(nc_file, 'r') as ds:
        # 获取文件名（不含扩展名）
        base_name = os.path.splitext(os.path.basename(nc_file))[0]
        
        # 读取所有变量数据
        data_dict = {}
        metadata = {}
        
        # 1. 读取维度信息
        metadata['dimensions'] = {}
        for dim_name, dim in ds.dimensions.items():
            metadata['dimensions'][dim_name] = {
                'size': dim.size,
                'isunlimited': dim.isunlimited()
            }
        
        # 2. 读取变量数据和属性
        metadata['variables'] = {}
        for var_name, var in ds.variables.items():
            # 读取数据
            data = var[:]
            data_dict[var_name] = data
            
            # 保存变量属性
            var_attrs = {}
            for attr_name in var.ncattrs():
                var_attrs[attr_name] = getattr(var, attr_name)
            
            metadata['variables'][var_name] = {
                'dtype': str(data.dtype),
                'shape': data.shape,
                'dimensions': var.dimensions,
                'attributes': var_attrs
            }
            
            # 特殊处理时间变量，转换为datetime
            if var_name == 'valid_time' and 'units' in var_attrs:
                try:
                    time_units = var_attrs['units']
                    calendar = var_attrs.get('calendar', 'standard')
                    datetime_array = num2date(data, units=time_units, calendar=calendar)
                    data_dict[f'{var_name}_datetime'] = datetime_array
                except Exception as e:
                    print(f"  警告：无法转换时间变量 {var_name} 为datetime: {e}")
        
        # 3. 保存为指定格式
        output_files = []
        
        if format == 'npy':
            # 保存为numpy格式
            for var_name, data in data_dict.items():
                output_file = os.path.join(output_dir, f"{base_name}_{var_name}.npy")
                np.save(output_file, data)
                output_files.append(output_file)
                print(f"  保存 {var_name} 到 {os.path.basename(output_file)}")
        
        elif format == 'pkl':
            # 保存为pickle格式（包含数据和元数据）
            save_dict = {
                'data': data_dict,
                'metadata': metadata
            }
            output_file = os.path.join(output_dir, f"{base_name}.pkl")
            with open(output_file, 'wb') as f:
                pickle.dump(save_dict, f)
            output_files.append(output_file)
            print(f"  保存到 {os.path.basename(output_file)}")
        
        elif format == 'csv':
            # 保存为CSV格式（仅适用于1D或2D数据）
            for var_name, data in data_dict.items():
                if data.ndim > 2:
                    print(f"  跳过 {var_name}：CSV仅支持1D或2D数据，当前维度 {data.ndim}")
                    continue
                
                output_file = os.path.join(output_dir, f"{base_name}_{var_name}.csv")
                
                if data.ndim == 1:
                    # 1D数据，保存为单列
                    np.savetxt(output_file, data, delimiter=',', header=var_name)
                else:
                    # 2D数据，保存为表格
                    np.savetxt(output_file, data, delimiter=',')
                
                output_files.append(output_file)
                print(f"  保存 {var_name} 到 {os.path.basename(output_file)}")
        
        else:
            print(f"  错误：不支持的格式 {format}")
            return None
        
        # 4. 生成字段说明文档
        generate_field_description(nc_file, data_dict, metadata, output_dir)
        
        print(f"  处理完成")
        
        return {
            'input_file': nc_file,
            'output_files': output_files,
            'format': format,
            'variables': list(data_dict.keys())
        }


def generate_field_description(nc_file, data_dict, metadata, output_dir):
    """
    生成字段说明文档
    
    参数:
        nc_file (str): 输入NetCDF文件路径
        data_dict (dict): 数据字典
        metadata (dict): 元数据
        output_dir (str): 输出文件夹路径
    """
    base_name = os.path.splitext(os.path.basename(nc_file))[0]
    desc_file = os.path.join(output_dir, f"{base_name}_field_description.txt")
    
    with open(desc_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("NetCDF文件字段说明\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"原始文件: {os.path.basename(nc_file)}\n")
        f.write(f"生成时间: {os.popen('date /t').read().strip()} {os.popen('time /t').read().strip()}\n\n")
        
        # 维度说明
        f.write("1. 维度信息\n")
        f.write("-" * 60 + "\n")
        for dim_name, dim_info in metadata['dimensions'].items():
            f.write(f"{dim_name}: 大小={dim_info['size']}, 无限维度={dim_info['isunlimited']}\n")
        f.write("\n")
        
        # 变量说明
        f.write("2. 变量信息\n")
        f.write("-" * 60 + "\n")
        for var_name, var_info in metadata['variables'].items():
            f.write(f"\n变量名称: {var_name}\n")
            f.write(f"  数据类型: {var_info['dtype']}\n")
            f.write(f"  形状: {var_info['shape']}\n")
            f.write(f"  维度: {var_info['dimensions']}\n")
            
            # 属性说明
            attrs = var_info['attributes']
            if attrs:
                f.write("  属性:\n")
                for attr_name, attr_value in attrs.items():
                    f.write(f"    {attr_name}: {attr_value}\n")
            
            # 数据示例
            data = data_dict[var_name]
            if data.size > 0:
                f.write("  数据示例:\n")
                if data.ndim == 0:
                    # 0维数组（标量）
                    f.write(f"    {data}\n")
                elif data.ndim == 1:
                    # 1维数组
                    sample = data[:min(5, len(data))]
                    f.write(f"    {sample}\n")
                elif data.ndim == 2:
                    # 2维数组
                    sample = data[:min(2, data.shape[0]), :min(3, data.shape[1])]
                    for row in sample:
                        f.write(f"    {row}\n")
                else:
                    # 高维数组，显示形状信息
                    f.write(f"    高维数组: 形状 {data.shape}\n")
        
        # 使用说明
        f.write("\n" + "=" * 80 + "\n")
        f.write("3. 使用说明\n")
        f.write("-" * 60 + "\n")
        f.write("\nPython调用示例:\n")
        f.write("\n# 1. 读取numpy文件\n")
        f.write("import numpy as np\n")
        f.write("data = np.load('filename_variable.npy')\n")
        f.write("print(data.shape)\n")
        f.write("print(data.dtype)\n")
        f.write("\n# 2. 读取pickle文件\n")
        f.write("import pickle\n")
        f.write("with open('filename.pkl', 'rb') as f:\n")
        f.write("    data_dict = pickle.load(f)\n")
        f.write("\n# 访问数据\n")
        f.write("u_wind = data_dict['u']  # U分量风场\n")
        f.write("pressure_levels = data_dict['pressure_level']  # 气压层\n")
        f.write("latitudes = data_dict['latitude']  # 纬度\n")
        f.write("longitudes = data_dict['longitude']  # 经度\n")
        f.write("times = data_dict['valid_time']  # 时间戳\n")
        f.write("time_datetime = data_dict.get('valid_time_datetime')  # 转换后的datetime\n")
        f.write("\n# 3. 数据结构说明\n")
        f.write("数据形状: (时间, 气压层, 纬度, 经度)\n")
        f.write("例如: u_wind[0, 1, :, :]  # 第一个时间点，第二个气压层的风场\n")
        
    print(f"  生成字段说明: {os.path.basename(desc_file)}")


def batch_convert(input_dir, output_dir, format='npy'):
    """
    批量转换文件夹中的NetCDF文件
    
    参数:
        input_dir (str): 输入文件夹路径
        output_dir (str): 输出文件夹路径
        format (str): 输出格式，可选 'npy', 'pkl', 'csv'
    """
    print("=" * 80)
    print(f"批量转换NetCDF文件到Python格式")
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"输出格式: {format}")
    print("=" * 80)
    
    # 获取所有nc文件
    nc_files = glob.glob(os.path.join(input_dir, "*.nc"))
    
    if not nc_files:
        print(f"错误: 在 {input_dir} 中未找到nc文件")
        return
    
    print(f"找到 {len(nc_files)} 个nc文件")
    
    # 处理每个文件
    success_count = 0
    for nc_file in nc_files:
        try:
            result = convert_nc_to_python(nc_file, output_dir, format)
            if result:
                success_count += 1
        except Exception as e:
            print(f"  错误: 处理文件 {os.path.basename(nc_file)} 失败: {e}")
    
    print("\n" + "=" * 80)
    print(f"批量转换完成")
    print(f"成功处理: {success_count}/{len(nc_files)} 个文件")
    print(f"输出目录: {output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='将NetCDF文件转换为Python方便调用的格式')
    parser.add_argument('--input', '-i', type=str, default='.', 
                        help='输入文件夹路径，默认当前目录')
    parser.add_argument('--output', '-o', type=str, default='python_format_data', 
                        help='输出文件夹路径，默认创建python_format_data目录')
    parser.add_argument('--format', '-f', type=str, default='npy', 
                        choices=['npy', 'pkl', 'csv'],
                        help='输出格式，可选 npy, pkl, csv，默认 npy')
    
    args = parser.parse_args()
    
    # 调用批量转换函数
    batch_convert(args.input, args.output, args.format)