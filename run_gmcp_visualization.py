#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GMCP降水数据可视化 - 自动保存版本
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from netCDF4 import Dataset
import os
import glob
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False


GMCP_BASE_PATH = r'G:/GMCP'
OUTPUT_DIR = r'e:/history_analysis/gmcp_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_gmcp_file(year, month, day, hour):
    """加载单个GMCP文件"""
    filename = f"GMCP_{year}_{month:02d}_{day:02d}_{hour:02d}.nc"
    filepath = os.path.join(GMCP_BASE_PATH, str(year), filename)

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    with Dataset(filepath, 'r') as ds:
        data_dict = {}
        for var_name in ds.variables:
            var = ds.variables[var_name]
            data_dict[var_name] = {
                'data': var[:],
                'units': getattr(var, 'units', 'unknown'),
                'long_name': getattr(var, 'long_name', var_name)
            }
        return data_dict


def visualize_single(year, month, day, hour, output_file):
    """可视化单个时次的降水数据"""
    print(f"\n[1/4] 生成单时次可视化: {year}-{month:02d}-{day:02d} {hour:02d}:00 UTC")

    data_dict = load_gmcp_file(year, month, day, hour)

    var_name = [k for k in data_dict.keys() if k not in ['longitude', 'latitude', 'time']][0]
    data = data_dict[var_name]['data']
    lon = data_dict['longitude']['data']
    lat = data_dict['latitude']['data']

    fig, ax = plt.subplots(figsize=(14, 9))

    X, Y = np.meshgrid(lon, lat)
    im = ax.pcolormesh(X, Y, data, cmap='Blues', norm=mcolors.LogNorm(vmin=0.1, vmax=50), shading='auto')

    cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label(f'{data_dict[var_name]["long_name"]} ({data_dict[var_name]["units"]})', fontsize=12)

    ax.set_xlabel('经度 (°E)', fontsize=12)
    ax.set_ylabel('纬度 (°N)', fontsize=12)
    ax.set_title(f'{data_dict[var_name]["long_name"]}\n{year}-{month:02d}-{day:02d} {hour:02d}:00 UTC',
                fontsize=14, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  ✓ 已保存: {output_file}")


def visualize_daily(year, month, day, output_file):
    """可视化日累计降水"""
    print(f"\n[2/4] 生成日累计降水: {year}-{month:02d}-{day:02d}")

    daily_data = None
    lon, lat, var_name = None, None, None

    for hour in range(24):
        try:
            data_dict = load_gmcp_file(year, month, day, hour)
            if daily_data is None:
                lon = data_dict['longitude']['data']
                lat = data_dict['latitude']['data']
                var_name = [k for k in data_dict.keys() if k not in ['longitude', 'latitude', 'time']][0]
                daily_data = np.zeros_like(data_dict[var_name]['data'], dtype=np.float32)

            hourly_data = data_dict[var_name]['data']
            if hasattr(hourly_data, 'filled'):
                hourly_data = hourly_data.filled(0)
            daily_data += hourly_data
        except FileNotFoundError:
            continue

    if daily_data is None:
        print("  ✗ 没有数据")
        return

    fig, ax = plt.subplots(figsize=(14, 9))

    X, Y = np.meshgrid(lon, lat)
    im = ax.pcolormesh(X, Y, daily_data, cmap='Blues', norm=mcolors.LogNorm(vmin=0.1, vmax=200), shading='auto')

    cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label('日累计降水量 (mm)', fontsize=12)

    ax.set_xlabel('经度 (°E)', fontsize=12)
    ax.set_ylabel('纬度 (°N)', fontsize=12)
    ax.set_title(f'日累计降水\n{year}-{month:02d}-{day:02d}', fontsize=14, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  ✓ 已保存: {output_file}")


def visualize_timeseries(year, month, day, output_file):
    """可视化时间序列"""
    print(f"\n[3/4] 生成时间序列: {year}-{month:02d}-{day:02d}")

    hours = [0, 6, 12, 18]
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    axes = axes.flatten()

    lon, lat, var_name, var_long_name, var_units = None, None, None, None, None

    for idx, hour in enumerate(hours):
        try:
            data_dict = load_gmcp_file(year, month, day, hour)

            if lon is None:
                lon = data_dict['longitude']['data']
                lat = data_dict['latitude']['data']
                var_name = [k for k in data_dict.keys() if k not in ['longitude', 'latitude', 'time']][0]
                var_long_name = data_dict[var_name]['long_name']
                var_units = data_dict[var_name]['units']

            data = data_dict[var_name]['data']

            im = axes[idx].pcolormesh(lon, lat, data, cmap='Blues',
                                     norm=mcolors.LogNorm(vmin=0.1, vmax=30), shading='auto')
            axes[idx].set_title(f'{hour:02d}:00 UTC', fontsize=12)
            axes[idx].set_xlabel('经度 (°E)', fontsize=10)
            axes[idx].set_ylabel('纬度 (°N)', fontsize=10)
            axes[idx].grid(True, linestyle='--', alpha=0.3)
            plt.colorbar(im, ax=axes[idx], shrink=0.6)

        except FileNotFoundError:
            axes[idx].text(0.5, 0.5, '无数据', ha='center', va='center', transform=axes[idx].transAxes)
            axes[idx].set_title(f'{hour:02d}:00 UTC', fontsize=12)

    fig.suptitle(f'{var_long_name} 时间序列\n{year}-{month:02d}-{day:02d}',
                fontsize=14, fontweight='bold', y=1.02)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  ✓ 已保存: {output_file}")


def visualize_region(year, month, day, hour, output_file):
    """可视化指定区域"""
    print(f"\n[4/4] 生成区域放大图: {year}-{month:02d}-{day:02d} {hour:02d}:00 UTC")

    lon_range = (104, 124)
    lat_range = (15, 30)

    data_dict = load_gmcp_file(year, month, day, hour)

    lon_all = data_dict['longitude']['data']
    lat_all = data_dict['latitude']['data']

    lon_mask = (lon_all >= lon_range[0]) & (lon_all <= lon_range[1])
    lat_mask = (lat_all >= lat_range[0]) & (lat_all <= lat_range[1])

    lon = lon_all[lon_mask]
    lat = lat_all[lat_mask]

    var_name = [k for k in data_dict.keys() if k not in ['longitude', 'latitude', 'time']][0]
    data = data_dict[var_name]['data'][lat_mask][:, lon_mask]

    fig, ax = plt.subplots(figsize=(12, 9))

    X, Y = np.meshgrid(lon, lat)
    im = ax.pcolormesh(X, Y, data, cmap='Blues', norm=mcolors.LogNorm(vmin=0.1, vmax=50), shading='auto')

    cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label(f'{data_dict[var_name]["long_name"]} ({data_dict[var_name]["units"]})', fontsize=12)

    ax.set_xlabel('经度 (°E)', fontsize=12)
    ax.set_ylabel('纬度 (°N)', fontsize=12)
    ax.set_title(f'{data_dict[var_name]["long_name"]}\n区域: {lon_range[0]}-{lon_range[1]}°E, {lat_range[0]}-{lat_range[1]}°N\n'
                f'{year}-{month:02d}-{day:02d} {hour:02d}:00 UTC', fontsize=14, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  ✓ 已保存: {output_file}")


def print_statistics(year, month, day):
    """打印统计信息"""
    print(f"\n{'='*60}")
    print(f"数据统计: {year}-{month:02d}-{day:02d}")
    print(f"{'='*60}")

    daily_data = None
    var_name = None

    for hour in range(24):
        try:
            data_dict = load_gmcp_file(year, month, day, hour)
            if daily_data is None:
                var_name = [k for k in data_dict.keys() if k not in ['longitude', 'latitude', 'time']][0]
                daily_data = np.zeros_like(data_dict[var_name]['data'], dtype=np.float32)

            data = data_dict[var_name]['data']
            if hasattr(data, 'filled'):
                data = data.filled(0)
            daily_data += data
        except:
            continue

    if daily_data is None:
        print("没有数据")
        return

    data = daily_data

    print(f"变量名: {var_name}")
    print(f"最小值: {np.nanmin(data):.4f} mm")
    print(f"最大值: {np.nanmax(data):.4f} mm")
    print(f"平均值: {np.nanmean(data):.4f} mm")
    print(f"中位数: {np.nanmedian(data):.4f} mm")
    print(f"标准差: {np.nanstd(data):.4f} mm")
    print(f"降水>0的网格点: {np.sum(data > 0)}")
    print(f"数据形状: {data.shape}")


def main():
    print("\n" + "="*60)
    print("GMCP降水数据可视化")
    print("="*60)
    print(f"数据路径: {GMCP_BASE_PATH}")
    print(f"输出路径: {OUTPUT_DIR}")

    year, month, day = 2000, 1, 1
    hour = 12

    try:
        print_statistics(year, month, day)

        visualize_single(year, month, day, hour,
                       os.path.join(OUTPUT_DIR, 'gmcp_01_single.png'))

        visualize_daily(year, month, day,
                       os.path.join(OUTPUT_DIR, 'gmcp_02_daily.png'))

        visualize_timeseries(year, month, day,
                            os.path.join(OUTPUT_DIR, 'gmcp_03_timeseries.png'))

        visualize_region(year, month, day, hour,
                        os.path.join(OUTPUT_DIR, 'gmcp_04_region.png'))

        print("\n" + "="*60)
        print("可视化完成！")
        print(f"所有图像已保存到: {OUTPUT_DIR}")
        print("="*60)

    except FileNotFoundError as e:
        print(f"\n警告: {e}")
        print("请检查G:/GMCP路径是否正确")

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
