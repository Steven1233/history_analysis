#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试转换后的Python格式文件是否可以正常读取
"""

import numpy as np
import pickle
import os

def test_pickle_file(pickle_file):
    """
    测试pickle文件的读取
    """
    print(f"\n测试文件: {os.path.basename(pickle_file)}")
    print("-" * 60)
    
    try:
        # 读取pickle文件
        with open(pickle_file, 'rb') as f:
            data_dict = pickle.load(f)
        
        print("成功读取pickle文件")
        
        # 打印数据结构
        if 'data' in data_dict and 'metadata' in data_dict:
            print("\n数据字典结构:")
            print(f"  - data: 包含 {len(data_dict['data'])} 个变量")
            print(f"  - metadata: 包含维度和变量元数据")
            
            # 打印变量列表
            print("\n变量列表:")
            for var_name in data_dict['data'].keys():
                data = data_dict['data'][var_name]
                print(f"  {var_name}: 形状 {data.shape}, 类型 {data.dtype}")
            
            # 显示一些示例数据
            print("\n数据示例:")
            if 'u' in data_dict['data']:
                u_data = data_dict['data']['u']
                print(f"  u分量风场: 最小值 {np.min(u_data):.4f}, 最大值 {np.max(u_data):.4f}")
                print(f"  数据形状: {u_data.shape}")
            
            if 'valid_time_datetime' in data_dict['data']:
                time_data = data_dict['data']['valid_time_datetime']
                print(f"  时间点: {[t.strftime('%Y-%m-%d %H:%M UTC') for t in time_data]}")
            
        else:
            print("\n数据字典结构:")
            for key, value in data_dict.items():
                if isinstance(value, np.ndarray):
                    print(f"  {key}: 形状 {value.shape}, 类型 {value.dtype}")
                else:
                    print(f"  {key}: 类型 {type(value).__name__}")
        
        print("\n测试通过!")
        return True
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        return False


def test_npy_file(npy_file):
    """
    测试npy文件的读取
    """
    print(f"\n测试文件: {os.path.basename(npy_file)}")
    print("-" * 60)
    
    try:
        # 读取npy文件
        data = np.load(npy_file)
        
        print("成功读取npy文件")
        print(f"  数据形状: {data.shape}")
        print(f"  数据类型: {data.dtype}")
        print(f"  数据范围: {np.min(data):.4f} 到 {np.max(data):.4f}")
        
        # 显示部分数据
        if data.ndim <= 2:
            print("  数据示例:")
            if data.ndim == 1:
                print(f"    {data[:5]}")
            else:
                for row in data[:2, :5]:
                    print(f"    {row}")
        
        print("\n测试通过!")
        return True
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        return False


if __name__ == "__main__":
    print("=" * 80)
    print("测试NetCDF转换后的Python格式文件")
    print("=" * 80)
    
    # 测试pickle文件
    pickle_dir = "H:/u_winds_2000_2024_extracted/era5.u_component_of_wind.20000401_py"
    if os.path.exists(pickle_dir):
        pickle_files = [os.path.join(pickle_dir, f) for f in os.listdir(pickle_dir) if f.endswith('.pkl')]
        
        if pickle_files:
            print(f"\n找到 {len(pickle_files)} 个pickle文件")
            for pickle_file in pickle_files:
                test_pickle_file(pickle_file)
        else:
            print(f"\n在 {pickle_dir} 中未找到pickle文件")
    
    # 测试npy文件
    npy_dir = "python_format_data"
    if os.path.exists(npy_dir):
        npy_files = [os.path.join(npy_dir, f) for f in os.listdir(npy_dir) if f.endswith('.npy')]
        
        if npy_files:
            print(f"\n找到 {len(npy_files)} 个npy文件")
            # 只测试前2个文件
            for npy_file in npy_files[:2]:
                test_npy_file(npy_file)
        else:
            print(f"\n在 {npy_dir} 中未找到npy文件")
    
    print("\n" + "=" * 80)
    print("所有测试完成")
    print("=" * 80)