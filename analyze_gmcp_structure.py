#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析G:/GMCP降水数据的NetCDF文件结构
"""

import numpy as np
from netCDF4 import Dataset
import os

def analyze_gmcp_structure(nc_file):
    """
    分析GMCP NetCDF文件的结构

    参数:
        nc_file (str): NetCDF文件路径
    """
    print("=" * 80)
    print(f"分析文件: {nc_file}")
    print("=" * 80)

    with Dataset(nc_file, 'r') as ds:
        print("\n[1. 文件基本信息]")
        print(f"  文件格式: {ds.file_format}")

        print("\n[2. 全局属性]")
        if ds.ncattrs():
            for attr in ds.ncattrs():
                print(f"  {attr}: {getattr(ds, attr)}")
        else:
            print("  该文件没有全局属性")

        print("\n[3. 维度信息]")
        print(f"  维度数量: {len(ds.dimensions)}")
        for dim_name, dim in ds.dimensions.items():
            print(f"    {dim_name}: size={dim.size}, unlimited={dim.isunlimited()}")

        print("\n[4. 变量信息]")
        print(f"  变量数量: {len(ds.variables)}")

        for var_name, var in ds.variables.items():
            print(f"\n  变量名称: {var_name}")
            print(f"    数据类型: {var.dtype}")
            print(f"    形状: {var.shape}")
            print(f"    维度: {var.dimensions}")
            print(f"    单位: {getattr(var, 'units', '无单位')}")
            print(f"    长名称: {getattr(var, 'long_name', '无长名称')}")

            var_attrs = var.ncattrs()
            if var_attrs:
                print(f"    属性列表:")
                for attr in var_attrs:
                    if attr not in ['units', 'long_name']:
                        print(f"      {attr}: {getattr(var, attr)}")

            if len(var.shape) > 0:
                slices = tuple(slice(0, min(3, dim)) for dim in var.shape)
                sample = var[slices]
                print(f"    数据样例: {sample}")
            else:
                print(f"    数据值: {var[:]}")

    print("\n" + "=" * 80)

def main():
    base_path = r'G:/GMCP'

    sample_file = os.path.join(base_path, '2000', 'GMCP_2000_01_01_00.nc')

    if os.path.exists(sample_file):
        analyze_gmcp_structure(sample_file)
    else:
        print(f"示例文件不存在: {sample_file}")

        for year in os.listdir(base_path):
            year_path = os.path.join(base_path, year)
            if os.path.isdir(year_path):
                for month in os.listdir(year_path)[:1]:
                    month_path = os.path.join(year_path, month)
                    if os.path.isdir(month_path):
                        for file in os.listdir(month_path)[:1]:
                            sample_file = os.path.join(month_path, file)
                            print(f"使用替代文件: {sample_file}")
                            analyze_gmcp_structure(sample_file)
                            break
                break

if __name__ == "__main__":
    main()
