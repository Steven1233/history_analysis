# ERA5气象数据相似性分析项目 Code Wiki

## 1. 项目概述

### 1.1 项目简介

本项目是一个基于ERA5再分析数据的天气模式相似性分析系统，主要用于处理气象学中的NetCDF格式数据，通过计算不同气象要素（如风场、相对湿度等）之间的相似度，找出历史上与特定日期最相似的天气模式。

### 1.2 核心功能

- **数据提取**：从ERA5再分析数据的NetCDF文件中按条件提取指定区域、时间、高度层的数据
- **数据转换**：将NetCDF格式转换为Python可用的numpy和pickle格式
- **相似性分析**：基于欧氏距离计算气象要素场的相似度
- **交集计算**：综合多个气象要素（U风、V风、相对湿度）找出共同相似日期
- **可视化展示**：生成热力图对比分析结果

### 1.3 数据范围

| 参数 | 范围 |
|------|------|
| 纬度范围 | 北纬15° - 30° |
| 经度范围 | 东经104° - 124° |
| 气压层 | 925hPa（低层）、500hPa（高层） |
| 时间点 | 00UTC、06UTC、12UTC、18UTC |
| 时间跨度 | 2000年 - 2024年 |

---

## 2. 项目架构

### 2.1 目录结构

```
history_analysis/
├── 数据处理模块
│   ├── process_era5_data.py        # 单文件ERA5数据处理
│   ├── process_era5_folders.py     # 批量文件夹ERA5数据处理
│   ├── extract_nc_data.py          # NetCDF数据提取
│   ├── batch_extract_nc.py         # 批量NetCDF数据提取
│   └── convert_nc_to_python.py     # 数据格式转换
│
├── 相似性分析模块
│   ├── similarity_analysis.py       # 核心相似性分析引擎
│   └── similarity_intersection.py   # 多要素交集分析
│
├── 数据查看与可视化模块
│   ├── nc_file_viewer.py           # NetCDF文件查看器
│   ├── view_npy_data.py            # NPY数据查看
│   ├── view_pkl_data.py            # PKL数据查看
│   ├── visualize_saved_results.py  # 结果可视化
│   └── analyze_intersection_file.py # 交集结果分析
│
├── 测试脚本
│   └── test_conversion.py          # 数据转换测试
│
└── 输出结果
    ├── *_similarity_results.npz    # 相似性分析结果
    ├── *_similarity.png            # 相似性热力图
    └── intersection_*_results.npz  # 交集分析结果
```

### 2.2 数据流向

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据输入层                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ ERA5 NC文件  │  │ ERA5 NC文件  │  │ ERA5 NC文件          │   │
│  │ (U分量风)    │  │ (V分量风)    │  │ (相对湿度)           │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
└─────────┼─────────────────┼─────────────────────┼───────────────┘
          │                 │                     │
          ▼                 ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据处理层                                │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │           process_era5_folders.py (批量处理)             │    │
│  │  - 区域筛选(104°E-124°E, 15°N-30°N)                      │    │
│  │  - 高度层筛选(925hPa, 500hPa)                            │    │
│  │  - 时间筛选(00/06/12/18UTC)                              │    │
│  │  - 格式转换(npy, pkl)                                    │    │
│  └──────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据存储层                                │
│  H:/u_winds_2000_2024_extracted/                               │
│  H:/v_winds_2000_2024_extracted/                               │
│  H:/relative_humidity_2000_2024_extracted/                    │
│                                                                 │
│  文件夹结构:                                                     │
│  └── era5.xxx.20000401_py/                                     │
│      ├── era5.xxx.20000401_u.pkl  (元数据+数据)                │
│      └── era5.xxx.20000401_u.npy  (纯数据)                     │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        分析层                                    │
│  ┌────────────────────────┐  ┌─────────────────────────────┐    │
│  │ similarity_analysis.py │  │ similarity_intersection.py  │    │
│  │ - 加载矩阵数据          │  │ - 获取各要素TopK相似日期     │    │
│  │ - 计算欧氏距离          │  │ - 计算交集日期              │    │
│  │ - 并行处理优化          │  │ - 多要素可视化              │    │
│  └────────────────────────┘  └─────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        输出层                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 结果文件 (*.npz) + 可视化图像 (*.png)                      │   │
│  │ - 输入条件热力图                                           │   │
│  │ - TopK相似模式热力图                                       │   │
│  │ - 交集日期对比图                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 3.1 数据处理模块

#### 3.1.1 `process_era5_data.py` - 单文件ERA5处理

**功能**：处理单个ERA5 NetCDF文件，提取指定条件数据并保存为多种格式。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `extract_and_save_data(nc_file)` | nc_file: str | bool | 提取单个NC文件数据并保存 |
| `generate_field_description(save_dict, output_dir, base_name, var_name)` | save_dict, output_dir, base_name, var_name | None | 生成字段说明文档 |

**数据提取条件**：
- 区域：东经104-124°，北纬15-30°
- 气压层：925hPa、500hPa
- 时间：00UTC、06UTC、12UTC、18UTC

**输出格式**：
1. NC格式：保持原始格式
2. Python格式：npy（numpy数组）和pkl（pickle包含元数据）

**使用示例**：

```python
from process_era5_data import extract_and_save_data

# 处理单个文件
success = extract_and_save_data('era5.relative_humidity.20000401.nc')
```

#### 3.1.2 `process_era5_folders.py` - 批量文件夹处理

**功能**：批量处理文件夹中的ERA5 NetCDF文件，支持多种气压层和时间变量命名。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `extract_and_save_data(nc_file, output_root_dir)` | nc_file, output_root_dir | bool | 提取并保存数据 |
| `process_folder(input_folder, output_root_dir)` | input_folder, output_root_dir | int | 处理文件夹中所有NC文件 |
| `generate_field_description(...)` | 同上 | None | 生成字段说明 |

**智能变量名识别**：
- 时间变量：`valid_time`、`time`
- 气压层变量：`pressure_level`、`level`、`isobaricInhPa`、`isobaric`

**使用示例**：

```python
from process_era5_folders import process_folder

# 处理指定文件夹
process_folder(
    r'H:\relative_humidity_2000_2024',
    r'H:'
)
```

#### 3.1.3 `convert_nc_to_python.py` - 数据格式转换

**功能**：将NetCDF文件转换为Python友好格式，支持npy、pkl、csv三种格式。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `convert_nc_to_python(nc_file, output_dir, format)` | nc_file, output_dir, format | dict | 单文件转换 |
| `generate_field_description(...)` | nc_file, data_dict, metadata, output_dir | None | 生成字段说明 |
| `batch_convert(input_dir, output_dir, format)` | input_dir, output_dir, format | None | 批量转换 |

**支持的输出格式**：

| 格式 | 说明 | 适用场景 |
|------|------|----------|
| `npy` | numpy二进制格式 | 高效数值计算 |
| `pkl` | pickle格式（含元数据） | 完整数据结构存储 |
| `csv` | 逗号分隔格式 | 仅支持1D/2D数据查看 |

**使用示例**：

```python
from convert_nc_to_python import batch_convert

# 批量转换为pickle格式
batch_convert(
    input_dir='./extracted_data',
    output_dir='./python_format_data',
    format='pkl'
)
```

### 3.2 相似性分析模块

#### 3.2.1 `similarity_analysis.py` - 核心相似性分析

**功能**：计算气象要素场的相似度，基于欧氏距离找出历史相似模式。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `calculate_euclidean_distance(matrix1, matrix2)` | matrix1, matrix2: ndarray | float | 计算欧氏距离 |
| `calculate_cosine_similarity(matrix1, matrix2)` | matrix1, matrix2: ndarray | float | 计算余弦相似度 |
| `load_matrix(variable, date, pressure_level, time_level)` | variable, date, pressure_level, time_level | ndarray | 加载指定条件矩阵 |
| `get_metadata_file_path(variable, date)` | variable, date | str | 获取元数据文件路径 |
| `get_all_dates(variable)` | variable | list | 获取所有可用日期 |
| `find_similar_patterns(variable, input_date, input_pressure_level, input_time_level, k)` | variable, date, pressure_level, time_level, k | list | 查找TopK相似模式 |
| `visualize_patterns(...)` | variable, input_date, pressure_level, time_level, similar_patterns | str | 生成可视化热力图 |
| `save_results(...)` | variable, input_date, pressure_level, time_level, similar_patterns | str | 保存结果到npz文件 |
| `analyze_similarity(variable, input_pressure_level, input_time_level, input_date, k)` | variable, pressure_level, time_level, date, k | tuple | 主分析函数 |

**配置常量**：

```python
# 数据路径映射
DATA_PATHS = {
    'u_winds': 'H:/u_winds_2000_2024_extracted',
    'v_winds': 'H:/v_winds_2000_2024_extracted',
    'relative_humidity': 'H:/relative_humidity_2000_2024_extracted'
}

# 高度层索引
PRESSURE_LEVELS = {
    '925hPa': 0,
    '500hPa': 1
}

# 时间索引
TIME_LEVELS = {
    '00UTC': 0,
    '06UTC': 1,
    '12UTC': 2,
    '18UTC': 3
}
```

**相似度计算方法**：

欧氏距离计算公式：
```
distance = ||matrix1 - matrix2|| = sqrt(sum((a_ij - b_ij)^2))
```

相似度转换为：
```
similarity = 1 / (1 + distance)
```

**并行处理**：
- 使用`multiprocessing.Pool`充分利用CPU多核
- 按日期批次并行计算相似度

**使用示例**：

```python
from similarity_analysis import analyze_similarity

# 分析相对湿度相似性
results, viz_path, save_path = analyze_similarity(
    variable='relative_humidity',
    input_pressure_level='925hPa',
    input_time_level='00UTC',
    input_date='20240629',
    k=5
)

# 打印结果
for i, result in enumerate(results, 1):
    print(f"{i}. 日期: {result['日期']}, 相似度: {result['相似度']:.4f}")
```

**输出结果结构**：

```python
{
    'input': {
        'variable': 'relative_humidity',
        'pressure_level': '925hPa',
        'time_level': '00UTC',
        'date': '20240629',
        'matrix': ndarray,
        'metadata': dict
    },
    'similar': [
        {
            'date': '20240526',
            'pressure_level': '925hPa',
            'time_level': '00UTC',
            'distance': 1523.45,
            'matrix': ndarray,
            'metadata': dict
        },
        ...
    ]
}
```

#### 3.2.2 `similarity_intersection.py` - 多要素交集分析

**功能**：综合分析U风、V风、相对湿度三个要素，找出三个要素同时相似的日期。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `get_similar_dates(variable, input_date, pressure_level, time_level, k)` | variable, date, pressure_level, time_level, k | list | 获取TopK相似日期列表 |
| `find_intersection(similar_dates_list)` | similar_dates_list: list[list] | list | 计算多要素交集日期 |
| `visualize_intersection(input_date, intersection_dates, pressure_level, time_level)` | input_date, dates, pressure_level, time_level | str | 可视化交集结果 |
| `save_intersection_results(input_date, intersection_dates, pressure_level, time_level)` | input_date, dates, pressure_level, time_level | str | 保存交集结果 |

**分析要素**：

```python
VARIABLES = ['u_winds', 'v_winds', 'relative_humidity']
```

**交集算法**：

```python
def find_intersection(similar_dates_list):
    intersection = set(similar_dates_list[0])
    for dates in similar_dates_list[1:]:
        intersection.intersection_update(set(dates))
    return sorted(list(intersection))
```

**使用示例**：

```python
from similarity_intersection import main

# 运行交集分析（交互式）
main()

# 或直接调用
from similarity_intersection import get_similar_dates, find_intersection

# 分别获取各要素的相似日期
u_similar = get_similar_dates('u_winds', '20240526', '925hPa', '00UTC', 30)
v_similar = get_similar_dates('v_winds', '20240526', '925hPa', '00UTC', 30)
rh_similar = get_similar_dates('relative_humidity', '20240526', '925hPa', '00UTC', 30)

# 计算交集
intersection = find_intersection([u_similar, v_similar, rh_similar])
print(f"三个要素同时相似的日期: {intersection}")
```

### 3.3 数据查看与可视化模块

#### 3.3.1 `nc_file_viewer.py` - NetCDF文件查看器

**功能**：面向初学者的NetCDF文件结构和内容分析工具。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `analyze_nc_file(file_path)` | file_path: str | None | 分析NetCDF文件结构 |

**分析内容**：
- 文件基本信息（路径、格式）
- 全局属性
- 维度信息（名称、大小、是否无限维度）
- 变量信息（类型、形状、单位、属性）
- 数据访问示例

**使用示例**：

```python
from nc_file_viewer import analyze_nc_file

# 分析NetCDF文件
analyze_nc_file('era5.u_component_of_wind.20000401.nc')

# 或通过命令行
# python nc_file_viewer.py era5.u_component_of_wind.20000401.nc
```

#### 3.3.2 `visualize_saved_results.py` - 结果可视化

**功能**：从已保存的npz结果文件重新生成可视化图像。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `visualize_saved_results(results_file)` | results_file: str | str | 可视化结果文件 |

**支持的输入格式**：
- `*_similarity_results.npz` - 单要素相似性结果
- `intersection_*_results.npz` - 多要素交集结果

**使用示例**：

```python
from visualize_saved_results import visualize_saved_results

# 可视化相似性结果
visualize_saved_results('relative_humidity_925hPa_00UTC_20240628_similarity_results.npz')

# 可视化交集结果
visualize_saved_results('intersection_925hPa_00UTC_20240628_results.npz')
```

#### 3.3.3 `analyze_intersection_file.py` - 交集结果分析

**功能**：快速查看交集结果文件内容。

**使用示例**：

```python
from analyze_intersection_file import analyze_intersection_file

analyze_intersection_file('intersection_925hPa_00UTC_20240628_results.npz')
```

### 3.4 测试脚本

#### 3.4.1 `test_conversion.py` - 数据转换测试

**功能**：验证转换后的npy和pkl文件是否可以正常读取。

**核心函数**：

| 函数名 | 参数 | 返回值 | 功能描述 |
|--------|------|--------|----------|
| `test_pickle_file(pickle_file)` | pickle_file: str | bool | 测试pickle文件读取 |
| `test_npy_file(npy_file)` | npy_file: str | bool | 测试npy文件读取 |

---

## 4. 数据结构

### 4.1 pickle数据格式

```python
{
    'variable_name': 'u',              # 变量名
    'variable_units': 'm s**-1',      # 单位
    'valid_time': ndarray,             # 时间戳数组
    'valid_time_datetime': ndarray,    # datetime对象数组
    'pressure_level': ndarray,         # 气压层数组 [925.0, 500.0]
    'latitude': ndarray,                # 纬度数组 (61个点)
    'longitude': ndarray,              # 经度数组 (81个点)
    'data': ndarray,                    # 数据数组
    'metadata': {
        'original_file': 'era5.xxx',
        'extraction_time': '2024-06-28',
        'extraction_area': '东经104-124, 北纬15-30',
        'time_points': ['00UTC', '06UTC', '12UTC', '18UTC'],
        'pressure_levels': ['925hPa', '500hPa']
    }
}
```

### 4.2 numpy数据格式

数据形状：`(4, 2, 61, 81)`

| 维度 | 大小 | 说明 |
|------|------|------|
| 0 | 4 | 时间点（00/06/12/18UTC） |
| 1 | 2 | 气压层（925hPa/500hPa） |
| 2 | 61 | 纬度点（15°N - 30°N） |
| 3 | 81 | 经度点（104°E - 124°E） |

### 4.3 相似性结果格式

```python
{
    'input': {
        'variable': 'relative_humidity',
        'pressure_level': '925hPa',
        'time_level': '00UTC',
        'date': '20240629',
        'matrix': ndarray,      # shape: (61, 81)
        'metadata': dict
    },
    'similar': [
        {
            'date': '20240526',
            'pressure_level': '925hPa',
            'time_level': '00UTC',
            'distance': 1523.45,
            'matrix': ndarray,
            'metadata': dict
        },
        ...
    ]
}
```

---

## 5. 依赖关系

### 5.1 Python环境要求

- Python 3.7+
- NumPy
- netCDF4
- Matplotlib
- pickle（Python标准库）

### 5.2 依赖安装

```bash
pip install numpy netCDF4 matplotlib
```

### 5.3 依赖关系图

```
similarity_intersection.py
├── similarity_analysis.py
│   ├── numpy
│   ├── pickle
│   ├── matplotlib.pyplot
│   └── multiprocessing.Pool
└── similarity_analysis (analyze_similarity, load_matrix)

visualize_saved_results.py
└── similarity_analysis.py

process_era5_data.py
├── numpy
├── netCDF4.Dataset
├── pickle
└── glob

process_era5_folders.py
├── numpy
├── netCDF4.Dataset
├── pickle
└── glob

convert_nc_to_python.py
├── numpy
├── pickle
├── csv
└── netCDF4.Dataset

nc_file_viewer.py
├── numpy
└── netCDF4.Dataset
```

---

## 6. 项目运行方式

### 6.1 数据预处理流程

#### 步骤1：批量处理ERA5文件夹

```bash
python process_era5_folders.py
```

或修改主函数中的路径后直接运行：

```python
input_folders = [
    r'H:\relative_humidity_2000_2024',
    r'H:\u_winds_2000_2024',
    r'H:\v_winds_2000_2024'
]
output_root_dir = r'H:'
```

#### 步骤2：验证数据转换结果

```bash
python test_conversion.py
```

### 6.2 相似性分析流程

#### 单要素相似性分析

```python
from similarity_analysis import analyze_similarity

# 分析相对湿度相似性
results, viz_path, save_path = analyze_similarity(
    variable='relative_humidity',
    input_pressure_level='925hPa',
    input_time_level='00UTC',
    input_date='20240629',
    k=5
)
```

#### 多要素交集分析

```python
from similarity_intersection import main

# 交互式运行
main()

# 预设参数运行
# 修改文件中的常量
VARIABLES = ['u_winds', 'v_winds', 'relative_humidity']
PRESSURE_LEVEL = '925hPa'
TIME_LEVEL = '00UTC'
INPUT_DATE = '20240526'
K = 30
```

### 6.3 结果查看流程

#### 查看NetCDF文件结构

```bash
python nc_file_viewer.py era5.u_component_of_wind.20000401.nc
```

#### 可视化已有结果

```python
from visualize_saved_results import visualize_saved_results

# 可视化相似性结果
visualize_saved_results('relative_humidity_925hPa_00UTC_20240628_similarity_results.npz')

# 可视化交集结果
visualize_saved_results('intersection_925hPa_00UTC_20240628_results.npz')
```

---

## 7. 关键算法说明

### 7.1 相似度计算算法

本项目采用**欧氏距离**作为相似度度量：

```python
def calculate_euclidean_distance(matrix1, matrix2):
    return np.linalg.norm(matrix1 - matrix2)
```

**为什么选择欧氏距离？**
- 直观易懂：表示两个向量在多维空间中的直线距离
- 适合网格化气象数据：气象要素场本身就是二维网格
- 归一化友好：结果可进一步转换为0-1相似度

**相似度转换**：
```
similarity = 1 / (1 + distance)
```
- 距离为0时，相似度为1（完全相同）
- 距离趋于无穷时，相似度趋于0

### 7.2 并行计算优化

```python
from multiprocessing import Pool, cpu_count

# 使用全部CPU核心
num_processes = cpu_count()

# 将日期分批并行处理
date_batches = [other_dates[i:i+batch_size] for i in range(0, len(other_dates), batch_size)]

with Pool(processes=num_processes) as pool:
    batch_results = pool.map(batch_process_dates, pool_args)
```

### 7.3 交集算法

```python
def find_intersection(similar_dates_list):
    intersection = set(similar_dates_list[0])
    for dates in similar_dates_list[1:]:
        intersection.intersection_update(set(dates))
    return sorted(list(intersection))
```

---

## 8. 使用注意事项

### 8.1 数据路径配置

确保在`similarity_analysis.py`中正确配置数据路径：

```python
DATA_PATHS = {
    'u_winds': 'H:/u_winds_2000_2024_extracted',
    'v_winds': 'H:/v_winds_2000_2024_extracted',
    'relative_humidity': 'H:/relative_humidity_2000_2024_extracted'
}
```

### 8.2 内存管理

- 相似性分析会加载多个日期的数据，确保有足够内存
- 建议K值设置在10-50之间，避免过多结果

### 8.3 中文字体配置

在可视化脚本中已配置中文字体：

```python
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False
```

如遇字体问题，可安装相应字体或修改为英文标签。

### 8.4 常见问题排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 无法找到pkl文件 | 路径错误或数据未转换 | 检查DATA_PATHS配置 |
| 变量名找不到 | ERA5文件版本差异 | 查看process_era5_folders.py的变量名列表 |
| 内存不足 | 分析日期范围过大 | 减少K值或分批处理 |
| 中文显示乱码 | 字体未安装 | 安装SimHei或修改为英文 |

---

## 9. 扩展开发指南

### 9.1 添加新的气象要素

1. 在`DATA_PATHS`中添加新要素路径
2. 在`VARIABLES`列表中添加新要素名称
3. 运行`process_era5_folders.py`处理新数据
4. 修改`similarity_intersection.py`使用新要素

### 9.2 自定义相似度算法

修改`similarity_analysis.py`中的相似度计算函数：

```python
def calculate_custom_similarity(matrix1, matrix2):
    # 实现自定义相似度算法
    # 例如：皮尔逊相关系数、曼哈顿距离等
    pass

def find_similar_patterns(...):
    # 替换欧氏距离计算
    # distance = calculate_custom_similarity(input_flat, current_flat)
    pass
```

### 9.3 添加新的可视化方式

```python
def visualize_advanced(input_matrix, similar_matrices, output_file):
    """高级可视化示例"""
    fig, axes = plt.subplots(2, 3)
    
    # 第1行：输入条件（U, V, RH）
    # 第2行：最相似日期对比
    
    plt.savefig(output_file)
    plt.close()
```

---

## 10. 参考资料

- [ERA5数据文档](https://confluence.ecmwf.int/display/CKB/ERA5+data+documentation)
- [netCDF4 Python库](https://unidata.github.io/netcdf4-python/)
- [Matplotlib官方文档](https://matplotlib.org/stable/contents.html)

---

*本文档由Code Wiki Generator自动生成*
*最后更新：2024年*
