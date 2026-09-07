#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GMCP降水数据可视化脚本

数据说明：
- 数据来源：G:/GMCP/
- 数据格式：NetCDF (.nc)
- 文件命名规则：GMCP_YYYY_MM_DD_HH.nc
- 时间分辨率：小时级别（每天24个时次，00-23UTC）
- 目录结构：按年份组织 (2000/, 2001/, ...)

功能：
1. 单时次降水可视化
2. 日累计降水
3. 多时次时间序列
4. 区域提取
5. 动画生成
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from netCDF4 import Dataset
import os
import glob
from datetime import datetime, timedelta
from matplotlib.animation import FuncAnimation, PillowWriter
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False


GMCP_BASE_PATH = r'G:/GMCP'

class GMCPDataViewer:
    """GMCP降水数据查看器"""

    def __init__(self, base_path=None):
        self.base_path = base_path or GMCP_BASE_PATH

    def load_single_file(self, year, month, day, hour):
        """
        加载单个GMCP文件

        参数:
            year (int): 年份
            month (int): 月份
            day (int): 日期
            hour (int): 小时 (0-23)

        返回:
            dict: 包含数据和元数据的字典
        """
        filename = f"GMCP_{year}_{month:02d}_{day:02d}_{hour:02d}.nc"
        filepath = os.path.join(self.base_path, str(year), filename)

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

    def get_file_path(self, year, month, day, hour):
        """获取文件路径"""
        filename = f"GMCP_{year}_{month:02d}_{day:02d}_{hour:02d}.nc"
        return os.path.join(self.base_path, str(year), filename)

    def list_available_dates(self, year, month=None):
        """
        列出可用日期

        参数:
            year (int): 年份
            month (int, optional): 月份，如果为None则列出全年

        返回:
            list: 日期字符串列表 ['YYYY-MM-DD', ...]
        """
        year_path = os.path.join(self.base_path, str(year))
        if not os.path.exists(year_path):
            return []

        if month:
            month_path = os.path.join(year_path, f"{month:02d}")
            pattern = os.path.join(month_path, "GMCP_*.nc")
        else:
            pattern = os.path.join(year_path, "*", "GMCP_*.nc")

        files = glob.glob(pattern)
        dates = set()

        for f in files:
            basename = os.path.basename(f)
            try:
                parts = basename.replace('GMCP_', '').replace('.nc', '').split('_')
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                dates.add(f"{y}-{m:02d}-{d:02d}")
            except:
                continue

        return sorted(list(dates))

    def visualize_single(self, year, month, day, hour, title=None,
                        vmin=0, vmax=50, cmap='Blues', figsize=(12, 8),
                        show_grid=True, output_file=None):
        """
        可视化单个时次的降水数据

        参数:
            year, month, day, hour: 时间参数
            title: 图表标题
            vmin, vmax: 颜色范围
            cmap: 颜色映射
            figsize: 图像大小
            show_grid: 是否显示网格
            output_file: 输出文件路径
        """
        data_dict = self.load_single_file(year, month, day, hour)

        var_name = [k for k in data_dict.keys() if k not in ['lon', 'lat', 'time']][0]
        var_info = data_dict[var_name]
        data = var_info['data']
        units = var_info['units']
        long_name = var_info['long_name']

        lon = data_dict['lon']['data']
        lat = data_dict['lat']['data']

        fig, ax = plt.subplots(figsize=figsize)

        X, Y = np.meshgrid(lon, lat)

        norm = mcolors.LogNorm(vmin=max(vmin, 0.1), vmax=vmax) if vmin >= 0 else None

        im = ax.pcolormesh(X, Y, data, cmap=cmap, norm=norm,
                          shading='auto')

        cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
        cbar.set_label(f'{long_name} ({units})', fontsize=12)

        ax.set_xlabel('Longitude (°E)', fontsize=12)
        ax.set_ylabel('Latitude (°N)', fontsize=12)

        if title is None:
            title = f'{long_name}\n{year}-{month:02d}-{day:02d} {hour:02d}:00 UTC'

        ax.set_title(title, fontsize=14, fontweight='bold')

        if show_grid:
            ax.grid(True, linestyle='--', alpha=0.5)

        ax.set_xlim([lon.min(), lon.max()])
        ax.set_ylim([lat.min(), lat.max()])

        plt.tight_layout()

        if output_file:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"图像已保存: {output_file}")

        plt.show()
        plt.close()

    def visualize_daily_accumulation(self, year, month, day,
                                    vmin=0, vmax=200, cmap='Blues',
                                    figsize=(12, 8), output_file=None):
        """
        可视化日累计降水

        参数:
            year, month, day: 日期
            vmin, vmax, cmap, figsize: 绘图参数
            output_file: 输出文件路径
        """
        print(f"加载 {year}-{month:02d}-{day:02d} 全天数据...")

        daily_data = None
        lon, lat = None, None

        for hour in range(24):
            try:
                data_dict = self.load_single_file(year, month, day, hour)

                var_name = [k for k in data_dict.keys()
                           if k not in ['lon', 'lat', 'time']][0]
                hourly_data = data_dict[var_name]['data']

                if lon is None:
                    lon = data_dict['lon']['data']
                    lat = data_dict['lat']['data']
                    daily_data = np.zeros_like(hourly_data, dtype=np.float32)

                if hasattr(hourly_data, 'filled'):
                    hourly_data = hourly_data.filled(0)

                daily_data += hourly_data

            except FileNotFoundError:
                print(f"  警告: {year}-{month:02d}-{day:02d} {hour:02d} 数据不存在")
                continue

        if daily_data is None:
            print("错误: 没有找到任何数据")
            return

        print(f"日累计完成，数据范围: {daily_data.min():.2f} - {daily_data.max():.2f}")

        fig, ax = plt.subplots(figsize=figsize)

        X, Y = np.meshgrid(lon, lat)

        norm = mcolors.LogNorm(vmin=max(vmin, 0.1), vmax=vmax) if vmin >= 0 else None

        im = ax.pcolormesh(X, Y, daily_data, cmap=cmap, norm=norm, shading='auto')

        cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
        cbar.set_label('Daily Accumulation (mm)', fontsize=12)

        ax.set_xlabel('Longitude (°E)', fontsize=12)
        ax.set_ylabel('Latitude (°N)', fontsize=12)
        ax.set_title(f'Daily Accumulated Precipitation\n{year}-{month:02d}-{day:02d}',
                    fontsize=14, fontweight='bold')

        ax.grid(True, linestyle='--', alpha=0.5)

        plt.tight_layout()

        if output_file:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"图像已保存: {output_file}")

        plt.show()
        plt.close()

    def visualize_time_series(self, year, month, day,
                             hours=None, nrows=3, ncols=4,
                             vmin=0, vmax=30, cmap='Blues',
                             figsize=(16, 12), output_file=None):
        """
        可视化多时次时间序列

        参数:
            year, month, day: 日期
            hours: 小时列表，默认 0, 6, 12, 18
            nrows, ncols: 子图行列数
            vmin, vmax, cmap, figsize: 绘图参数
            output_file: 输出文件路径
        """
        if hours is None:
            hours = [0, 6, 12, 18]

        n_subplots = len(hours)
        ncols = min(ncols, n_subplots)
        nrows = (n_subplots + ncols - 1) // ncols

        fig, axes = plt.subplots(nrows, ncols, figsize=figsize)
        if n_subplots == 1:
            axes = np.array([axes])
        axes = axes.flatten()

        lon, lat = None, None
        var_name = None
        var_units = None
        var_long_name = None

        for idx, hour in enumerate(hours):
            try:
                data_dict = self.load_single_file(year, month, day, hour)

                if lon is None:
                    lon = data_dict['lon']['data']
                    lat = data_dict['lat']['data']
                    var_name = [k for k in data_dict.keys()
                               if k not in ['lon', 'lat', 'time']][0]
                    var_units = data_dict[var_name]['units']
                    var_long_name = data_dict[var_name]['long_name']

                data = data_dict[var_name]['data']

                ax = axes[idx]

                norm = mcolors.LogNorm(vmin=max(vmin, 0.1), vmax=vmax) if vmin >= 0 else None
                im = ax.pcolormesh(lon, lat, data, cmap=cmap, norm=norm, shading='auto')

                ax.set_title(f'{hour:02d}:00 UTC', fontsize=11)
                ax.set_xlabel('Lon (°E)', fontsize=9)
                ax.set_ylabel('Lat (°N)', fontsize=9)
                ax.grid(True, linestyle='--', alpha=0.3)

                plt.colorbar(im, ax=ax, shrink=0.6)

            except FileNotFoundError:
                ax = axes[idx]
                ax.text(0.5, 0.5, 'No Data', ha='center', va='center',
                       transform=ax.transAxes)
                ax.set_title(f'{hour:02d}:00 UTC (No Data)', fontsize=11)

        for idx in range(len(hours), len(axes)):
            axes[idx].set_visible(False)

        fig.suptitle(f'{var_long_name} Time Series\n{year}-{month:02d}-{day:02d}',
                    fontsize=14, fontweight='bold', y=1.02)

        plt.tight_layout()

        if output_file:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"图像已保存: {output_file}")

        plt.show()
        plt.close()

    def visualize_region(self, year, month, day, hour,
                        lon_range=None, lat_range=None,
                        vmin=0, vmax=30, cmap='Blues',
                        figsize=(12, 8), output_file=None):
        """
        可视化指定区域的数据

        参数:
            lon_range: (min_lon, max_lon)
            lat_range: (min_lat, max_lat)
        """
        if lon_range is None:
            lon_range = (104, 124)
        if lat_range is None:
            lat_range = (15, 30)

        data_dict = self.load_single_file(year, month, day, hour)

        lon_all = data_dict['lon']['data']
        lat_all = data_dict['lat']['data']

        lon_mask = (lon_all >= lon_range[0]) & (lon_all <= lon_range[1])
        lat_mask = (lat_all >= lat_range[0]) & (lat_all <= lat_range[1])

        lon = lon_all[lon_mask]
        lat = lat_all[lat_mask]

        var_name = [k for k in data_dict.keys()
                   if k not in ['lon', 'lat', 'time']][0]
        data = data_dict[var_name]['data'][lat_mask][:, lon_mask]

        fig, ax = plt.subplots(figsize=figsize)

        X, Y = np.meshgrid(lon, lat)

        norm = mcolors.LogNorm(vmin=max(vmin, 0.1), vmax=vmax) if vmin >= 0 else None
        im = ax.pcolormesh(X, Y, data, cmap=cmap, norm=norm, shading='auto')

        cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
        cbar.set_label(f'{data_dict[var_name]["long_name"]} ({data_dict[var_name]["units"]})',
                      fontsize=12)

        ax.set_xlabel('Longitude (°E)', fontsize=12)
        ax.set_ylabel('Latitude (°N)', fontsize=12)
        ax.set_title(f'{data_dict[var_name]["long_name"]}\n'
                    f'Region: {lon_range[0]}-{lon_range[1]}°E, {lat_range[0]}-{lat_range[1]}°N\n'
                    f'{year}-{month:02d}-{day:02d} {hour:02d}:00 UTC',
                    fontsize=14, fontweight='bold')

        ax.grid(True, linestyle='--', alpha=0.5)

        plt.tight_layout()

        if output_file:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"图像已保存: {output_file}")

        plt.show()
        plt.close()

    def visualize_animation(self, year, month, day,
                            hours=None, vmin=0, vmax=30, cmap='Blues',
                            interval=500, output_file=None):
        """
        创建降水动画

        参数:
            hours: 小时列表
            interval: 帧间隔(ms)
            output_file: 输出gif文件路径
        """
        if hours is None:
            hours = list(range(24))

        print("加载动画数据...")

        frames_data = []
        lon, lat = None, None

        for hour in hours:
            try:
                data_dict = self.load_single_file(year, month, day, hour)

                if lon is None:
                    lon = data_dict['lon']['data']
                    lat = data_dict['lat']['data']

                var_name = [k for k in data_dict.keys()
                           if k not in ['lon', 'lat', 'time']][0]
                data = data_dict[var_name]['data']
                frames_data.append(data)

            except FileNotFoundError:
                continue

        if not frames_data:
            print("错误: 没有数据")
            return

        print(f"加载了 {len(frames_data)} 帧")

        fig, ax = plt.subplots(figsize=(12, 8))

        X, Y = np.meshgrid(lon, lat)
        norm = mcolors.LogNorm(vmin=max(vmin, 0.1), vmax=vmax) if vmin >= 0 else None

        im = ax.pcolormesh(X, Y, frames_data[0], cmap=cmap, norm=norm, shading='auto')
        plt.colorbar(im, ax=ax, shrink=0.8)

        ax.set_xlabel('Longitude (°E)', fontsize=12)
        ax.set_ylabel('Latitude (°N)', fontsize=12)
        ax.set_title(f'{year}-{month:02d}-{day:02d} 00:00 UTC',
                    fontsize=14, fontweight='bold')
        ax.grid(True, linestyle='--', alpha=0.5)

        def init():
            im.set_array(frames_data[0])
            return [im]

        def animate(frame_idx):
            im.set_array(frames_data[frame_idx])
            hour = hours[frame_idx]
            ax.set_title(f'{year}-{month:02d}-{day:02d} {hour:02d}:00 UTC',
                        fontsize=14, fontweight='bold')
            return [im]

        anim = FuncAnimation(fig, animate, init_func=init,
                            frames=len(frames_data), interval=interval,
                            blit=True)

        if output_file:
            print(f"保存动画到: {output_file}")
            anim.save(output_file, writer=PillowWriter(fps=2))
            print("动画保存完成")

        plt.close()
        return anim

    def get_statistics(self, year, month, day, hour=None):
        """
        获取数据统计信息

        参数:
            year, month, day: 日期
            hour: 如果指定则统计单时次，否则统计日累计
        """
        if hour is not None:
            data_dict = self.load_single_file(year, month, day, hour)
            var_name = [k for k in data_dict.keys()
                       if k not in ['lon', 'lat', 'time']][0]
            data = data_dict[var_name]['data']

            if hasattr(data, 'filled'):
                data = data.filled(np.nan)

            print(f"\n{'='*60}")
            print(f"单时次统计: {year}-{month:02d}-{day:02d} {hour:02d}:00 UTC")
            print(f"{'='*60}")
        else:
            daily_data = None
            for h in range(24):
                try:
                    data_dict = self.load_single_file(year, month, day, h)
                    if daily_data is None:
                        var_name = [k for k in data_dict.keys()
                                   if k not in ['lon', 'lat', 'time']][0]
                        daily_data = np.zeros_like(data_dict[var_name]['data'], dtype=np.float32)

                    data = data_dict[var_name]['data']
                    if hasattr(data, 'filled'):
                        data = data.filled(0)
                    daily_data += data
                except:
                    continue

            data = daily_data
            print(f"\n{'='*60}")
            print(f"日累计统计: {year}-{month:02d}-{day:02d}")
            print(f"{'='*60}")

        print(f"变量名: {var_name}")
        print(f"最小值: {np.nanmin(data):.4f}")
        print(f"最大值: {np.nanmax(data):.4f}")
        print(f"平均值: {np.nanmean(data):.4f}")
        print(f"中位数: {np.nanmedian(data):.4f}")
        print(f"标准差: {np.nanstd(data):.4f}")
        print(f"有效网格点数: {np.sum(~np.isnan(data))}")
        print(f"降水>0的网格点数: {np.sum((~np.isnan(data)) & (data > 0))}")
        print(f"数据形状: {data.shape}")


def demo():
    """演示脚本用法"""
    viewer = GMCPDataViewer()

    print("\n" + "="*60)
    print("GMCP降水数据可视化演示")
    print("="*60)

    year, month, day = 2000, 1, 1
    hour = 12

    try:
        print("\n1. 单时次可视化示例")
        viewer.visualize_single(year, month, day, hour,
                               title='GMCP Precipitation Sample',
                               output_file='gmcp_sample_single.png')

        print("\n2. 日累计降水可视化示例")
        viewer.visualize_daily_accumulation(year, month, day,
                                           output_file='gmcp_sample_daily.png')

        print("\n3. 时间序列可视化示例")
        viewer.visualize_time_series(year, month, day,
                                    hours=[0, 6, 12, 18],
                                    output_file='gmcp_sample_timeseries.png')

        print("\n4. 区域放大可视化示例（中国区域）")
        viewer.visualize_region(year, month, day, hour,
                               lon_range=(104, 124),
                               lat_range=(15, 30),
                               output_file='gmcp_sample_region.png')

        print("\n5. 数据统计信息")
        viewer.get_statistics(year, month, day)
        viewer.get_statistics(year, month, day, hour=12)

        print("\n" + "="*60)
        print("演示完成！")
        print("="*60)

    except FileNotFoundError as e:
        print(f"\n警告: {e}")
        print("请检查G:/GMCP路径是否正确，或修改脚本中的GMCP_BASE_PATH")

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='GMCP降水数据可视化工具')
    parser.add_argument('--mode', '-m', type=str, default='demo',
                       choices=['demo', 'single', 'daily', 'timeseries', 'region', 'anim', 'stat'],
                       help='运行模式')
    parser.add_argument('--year', '-y', type=int, default=2000,
                       help='年份')
    parser.add_argument('--month', '-M', type=int, default=1,
                       help='月份')
    parser.add_argument('--day', '-d', type=int, default=1,
                       help='日期')
    parser.add_argument('--hour', '-H', type=int, default=12,
                       help='小时 (0-23)')
    parser.add_argument('--output', '-o', type=str, default=None,
                       help='输出文件路径')
    parser.add_argument('--lon-min', type=float, default=104,
                       help='最小经度')
    parser.add_argument('--lon-max', type=float, default=124,
                       help='最大经度')
    parser.add_argument('--lat-min', type=float, default=15,
                       help='最小纬度')
    parser.add_argument('--lat-max', type=float, default=30,
                       help='最大纬度')

    args = parser.parse_args()

    viewer = GMCPDataViewer()

    if args.mode == 'demo':
        demo()
    elif args.mode == 'single':
        viewer.visualize_single(args.year, args.month, args.day, args.hour,
                               output_file=args.output)
    elif args.mode == 'daily':
        viewer.visualize_daily_accumulation(args.year, args.month, args.day,
                                           output_file=args.output)
    elif args.mode == 'timeseries':
        viewer.visualize_time_series(args.year, args.month, args.day,
                                    output_file=args.output)
    elif args.mode == 'region':
        viewer.visualize_region(args.year, args.month, args.day, args.hour,
                              lon_range=(args.lon_min, args.lon_max),
                              lat_range=(args.lat_min, args.lat_max),
                              output_file=args.output)
    elif args.mode == 'anim':
        viewer.visualize_animation(args.year, args.month, args.day,
                                   output_file=args.output or 'gmcp_animation.gif')
    elif args.mode == 'stat':
        viewer.get_statistics(args.year, args.month, args.day, args.hour)
