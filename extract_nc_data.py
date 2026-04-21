#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
提取NetCDF文件中指定条件的数据

提取条件：
- 区域范围：东经104-124，北纬15-30
- 高度层：925hPa和500hPa
- 时间：00UTC, 06UTC, 12UTC, 18UTC
"""

import numpy as np
from netCDF4 import Dataset, num2date
import os


def extract_nc_data(input_file, output_file):
    """
    提取NetCDF文件中指定条件的数据
    
    参数:
        input_file (str): 输入NetCDF文件路径
        output_file (str): 输出NetCDF文件路径
    """
    print("=" * 80)
    print(f"开始提取数据: {input_file}")
    print("=" * 80)
    
    # 打开输入NetCDF文件
    with Dataset(input_file, 'r') as src:
        # 1. 获取维度数据
        valid_time = src.variables['valid_time'][:]
        pressure_level = src.variables['pressure_level'][:]
        latitude = src.variables['latitude'][:]
        longitude = src.variables['longitude'][:]
        
        # 2. 筛选条件
        # 时间筛选：00UTC, 06UTC, 12UTC, 18UTC
        # 转换时间戳为datetime对象
        time_units = src.variables['valid_time'].units
        time_calendar = src.variables['valid_time'].calendar
        datetime_array = num2date(valid_time, units=time_units, calendar=time_calendar)
        
        # 筛选出指定UTC时间的索引
        time_indices = []
        for i, dt in enumerate(datetime_array):
            if dt.hour in [0, 6, 12, 18]:
                time_indices.append(i)
        
        print(f"筛选到的时间点数量: {len(time_indices)}")
        print(f"时间点: {[datetime_array[i].strftime('%Y-%m-%d %H:%M UTC') for i in time_indices]}")
        
        # 气压层筛选：925hPa和500hPa
        pressure_indices = []
        for i, pl in enumerate(pressure_level):
            if pl in [925.0, 500.0]:
                pressure_indices.append(i)
        
        print(f"筛选到的气压层级: {[pressure_level[i] for i in pressure_indices]} hPa")
        
        # 纬度筛选：北纬15-30
        # 注意：数据中的纬度是从北向南递减的
        lat_indices = np.where((latitude >= 15.0) & (latitude <= 30.0))[0]
        
        print(f"筛选到的纬度范围: {latitude[lat_indices[0]]:.2f}°N 到 {latitude[lat_indices[-1]]:.2f}°N")
        print(f"纬度点数量: {len(lat_indices)}")
        
        # 经度筛选：东经104-124
        lon_indices = np.where((longitude >= 104.0) & (longitude <= 124.0))[0]
        
        print(f"筛选到的经度范围: {longitude[lon_indices[0]]:.2f}°E 到 {longitude[lon_indices[-1]]:.2f}°E")
        print(f"经度点数量: {len(lon_indices)}")
        
        # 3. 创建输出NetCDF文件
        with Dataset(output_file, 'w', format='NETCDF4') as dst:
            # 4. 复制全局属性
            for attr_name in src.ncattrs():
                setattr(dst, attr_name, getattr(src, attr_name))
            
            # 5. 创建维度
            # 时间维度
            dst.createDimension('valid_time', len(time_indices))
            # 气压层维度
            dst.createDimension('pressure_level', len(pressure_indices))
            # 纬度维度
            dst.createDimension('latitude', len(lat_indices))
            # 经度维度
            dst.createDimension('longitude', len(lon_indices))
            # 其他维度（如果需要）
            if 'number' in src.dimensions:
                dst.createDimension('number', src.dimensions['number'].size)
            if 'expver' in src.dimensions:
                dst.createDimension('expver', src.dimensions['expver'].size if not src.dimensions['expver'].isunlimited() else None)
            
            # 6. 创建变量
            # 通用函数：创建变量并复制属性
            def create_and_copy_var(var_name, dims):
                src_var = src.variables[var_name]
                fill_value = getattr(src_var, '_FillValue', None)
                dst_var = dst.createVariable(var_name, src_var.dtype, dims, fill_value=fill_value)
                for attr_name in src_var.ncattrs():
                    if attr_name != '_FillValue':
                        setattr(dst_var, attr_name, getattr(src_var, attr_name))
                return dst_var
            
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
            
            # 7. 提取风场数据
            for var_name in ['u', 'v']:
                if var_name in src.variables:
                    print(f"\n提取变量: {var_name}")
                    
                    # 获取源变量的FillValue
                    fill_value = getattr(src.variables[var_name], '_FillValue', None)
                    
                    # 创建目标变量，显式设置fill_value
                    dst_var = dst.createVariable(var_name, src.variables[var_name].dtype, 
                                               ('valid_time', 'pressure_level', 'latitude', 'longitude'),
                                               fill_value=fill_value)
                    
                    # 复制变量属性（除了_FillValue，因为已经在createVariable中设置）
                    for attr_name in src.variables[var_name].ncattrs():
                        if attr_name != '_FillValue':
                            setattr(dst_var, attr_name, getattr(src.variables[var_name], attr_name))
                    
                    # 提取数据
                    # 原始数据形状: (time, pressure, lat, lon)
                    data = src.variables[var_name][time_indices, :, :, :]
                    data = data[:, pressure_indices, :, :]
                    data = data[:, :, lat_indices, :]
                    data = data[:, :, :, lon_indices]
                    
                    # 写入数据
                    dst_var[:] = data
                    
                    print(f"  数据形状: {data.shape}")
                    print(f"  数据范围: {np.min(data):.4f} 到 {np.max(data):.4f} {src.variables[var_name].units}")
        
    print("\n" + "=" * 80)
    print(f"数据提取完成，保存到: {output_file}")
    print("=" * 80)


if __name__ == "__main__":
    # 设置输入输出文件路径
    input_file = 'era5.u_component_of_wind.20000401.nc'
    output_file = 'extracted_era5_data.nc'
    
    # 检查输入文件是否存在
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        print("请检查文件路径是否正确")
        exit(1)
    
    # 调用提取函数
    try:
        extract_nc_data(input_file, output_file)
    except Exception as e:
        print(f"提取数据时出错: {e}")
        print("请确保已安装必要的库:")
        print("  pip install numpy netCDF4")
        exit(1)
    
    print("\n所有操作完成!")