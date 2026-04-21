#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
NetCDF文件查看器 - 面向初学者的详细教程

这个脚本用于帮助初学者理解和分析NetCDF文件的结构和内容。
NetCDF(Network Common Data Form)是一种用于存储多维科学数据的格式，
常用于气象学、海洋学、地球科学等领域。
"""

# 导入必要的库
import numpy as np          # 用于科学计算和数组操作
import netCDF4 as nc        # NetCDF4库，用于处理NetCDF文件
from netCDF4 import Dataset  # 用于打开和操作NetCDF文件


def analyze_nc_file(file_path):
    """
    分析NetCDF文件的结构和内容
    
    参数:
        file_path (str): NetCDF文件的路径
    """
    print("=" * 80)
    print("开始分析NetCDF文件")
    print("=" * 80)
    
    # 使用上下文管理器打开NetCDF文件，自动处理文件关闭
    with Dataset(file_path, 'r') as nc_dataset:
        # 1. 打印文件的基本信息
        print("\n[1. 文件基本信息]")
        print(f"文件路径: {nc_dataset.filepath()}")
        print(f"文件格式: {nc_dataset.file_format}")
        
        # 2. 查看全局属性
        print("\n[2. 全局属性]")
        if nc_dataset.ncattrs():
            for attr in nc_dataset.ncattrs():
                print(f"  {attr}: {getattr(nc_dataset, attr)}")
        else:
            print("  该文件没有全局属性")
        
        # 3. 分析维度
        print("\n[3. 维度信息]")
        print(f"维度数量: {len(nc_dataset.dimensions)}")
        print(f"维度名称列表: {list(nc_dataset.dimensions.keys())}")
        print("\n各维度详细信息:")
        for dim_name, dim in nc_dataset.dimensions.items():
            print(f"  维度名称: {dim_name}")
            print(f"    大小: {dim.size}")
            print(f"    是无限维度(时间维度通常为无限): {dim.isunlimited()}")
        
        # 4. 分析变量
        print("\n[4. 变量信息]")
        print(f"变量数量: {len(nc_dataset.variables)}")
        print(f"变量名称列表: {list(nc_dataset.variables.keys())}")
        
        # 详细分析每个变量
        for var_name, var in nc_dataset.variables.items():
            print(f"\n  变量名称: {var_name}")
            print(f"    数据类型: {var.dtype}")
            print(f"    形状: {var.shape}")
            print(f"    维度: {var.dimensions}")
            print(f"    数据单位: {getattr(var, 'units', '无单位信息')}")
            print(f"    填充值: {getattr(var, '_FillValue', '无填充值')}")
            
            # 变量属性
            var_attrs = var.ncattrs()
            if var_attrs:
                print(f"    属性列表 ({len(var_attrs)}个):")
                for attr in var_attrs:
                    print(f"      {attr}: {getattr(var, attr)}")
            else:
                print("    无属性信息")
            
            # 显示变量的一小部分数据样例
            try:
                # 对于多维数据，我们只获取一小部分数据来显示
                if len(var.shape) > 0:
                    # 计算切片的索引，获取数据的一小部分
                    slices = tuple(slice(0, min(3, dim)) for dim in var.shape)
                    sample_data = var[slices]
                    print(f"    数据样例(前3个元素): {sample_data}")
                else:
                    print(f"    数据值: {var[:]}")
            except Exception as e:
                print(f"    无法显示数据样例: {e}")
        
        # 5. 数据访问示例
        print("\n[5. 数据访问示例]")
        # 选择第一个变量作为示例
        if len(nc_dataset.variables) > 0:
            first_var_name = list(nc_dataset.variables.keys())[0]
            first_var = nc_dataset.variables[first_var_name]
            print(f"  以变量 '{first_var_name}' 为例说明如何访问数据:")
            
            # 根据维度数量提供不同的数据访问示例
            if len(first_var.shape) == 1:
                print(f"    # 获取整个一维数组")
                print(f"    data = {first_var_name}[:]")
                print(f"    # 获取特定索引的值")
                print(f"    value = {first_var_name}[0]")
            elif len(first_var.shape) == 2:
                print(f"    # 获取整个二维数组")
                print(f"    data = {first_var_name}[:,:]")
                print(f"    # 获取第一行数据")
                print(f"    row_data = {first_var_name}[0,:]")
                print(f"    # 获取第一列数据")
                print(f"    col_data = {first_var_name}[:,0]")
            else:
                print(f"    # 对于{len(first_var.shape)}维数据，可以使用切片访问")
                slice_str = ",".join([":"] * len(first_var.shape))
                print(f"    data = {first_var_name}[{slice_str}]")
                
                # 提供一个更具体的切片示例
                slice_example = []
                for i, dim_size in enumerate(first_var.shape):
                    if i == 0:
                        slice_example.append("0")  # 第一个维度取第一个值
                    else:
                        slice_example.append(":")   # 其他维度取全部
                slice_example_str = ",".join(slice_example)
                print(f"    # 例如，获取第一个{len(first_var.shape)-1}维切片")
                print(f"    slice_data = {first_var_name}[{slice_example_str}]")
        
        print("\n" + "=" * 80)
        print("NetCDF文件分析完成")
        print("=" * 80)


if __name__ == "__main__":
    import sys
    
    # 设置要分析的NetCDF文件路径
    if len(sys.argv) > 1:
        # 从命令行参数获取文件路径
        nc_file_path = sys.argv[1]
    else:
        # 默认文件路径
        nc_file_path = 'era5.u_component_of_wind.20000401.nc'
    
    print(f"分析文件: {nc_file_path}")
    
    # 调用分析函数
    try:
        analyze_nc_file(nc_file_path)
    except Exception as e:
        print(f"分析文件时出错: {e}")
        print("请确保文件路径正确，并且已安装必要的库:")
        print("  pip install numpy netCDF4")
