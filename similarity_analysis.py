import numpy as np
import os
import pickle
import matplotlib.pyplot as plt
from datetime import datetime
from multiprocessing import Pool, cpu_count

# 数据路径映射
DATA_PATHS = {
    'u_winds': 'H:/u_winds_2000_2024_extracted',
    'v_winds': 'H:/v_winds_2000_2024_extracted',
    'relative_humidity': 'H:/relative_humidity_2000_2024_extracted'
}

# 高度层映射（索引）
PRESSURE_LEVELS = {
    '925hPa': 0,
    '500hPa': 1
}

# 时间映射（索引）
TIME_LEVELS = {
    '00UTC': 0,
    '06UTC': 1,
    '12UTC': 2,
    '18UTC': 3
}

def calculate_euclidean_distance(matrix1, matrix2):
    """
    计算两个矩阵之间的欧式距离
    距离越小，相似度越高
    """
    return np.linalg.norm(matrix1 - matrix2)

def get_metadata_file_path(variable, date):
    """
    根据要素和日期获取元数据文件路径（只在带_py的文件夹中查找）
    """
    data_dir = DATA_PATHS[variable]
    
    # 查找所有带_py的文件夹
    for item in os.listdir(data_dir):
        if '_py' in item and os.path.isdir(os.path.join(data_dir, item)):
            folder_path = os.path.join(data_dir, item)
            # 检查文件夹名称是否包含日期
            if date in item:
                # 查找pkl文件
                for file in os.listdir(folder_path):
                    if file.endswith('.pkl'):
                        return os.path.join(folder_path, file)
    return None

def load_matrix(variable, date, pressure_level, time_level):
    """
    加载指定条件的矩阵数据（从.pkl文件中加载）
    """
    # 获取元数据文件路径
    metadata_file = get_metadata_file_path(variable, date)
    if not metadata_file:
        raise FileNotFoundError(f"找不到{variable}在{date}的.pkl文件")
    
    # 加载数据
    with open(metadata_file, 'rb') as f:
        data_dict = pickle.load(f)
    
    # 尝试多种可能的数据键名
    data = None
    
    # 直接使用data键（根据原始代码中的使用方式）
    if 'data' in data_dict:
        data = data_dict['data']
    # 尝试根据变量名获取
    elif variable == 'u_winds':
        # 尝试多种可能的键名
        for key in ['u_wind', 'u', 'uwind', 'U']:
            if key in data_dict:
                data = data_dict[key]
                break
    elif variable == 'v_winds':
        # 尝试多种可能的键名
        for key in ['v_wind', 'v', 'vwind', 'V']:
            if key in data_dict:
                data = data_dict[key]
                break
    else:  # relative_humidity
        # 尝试多种可能的键名
        for key in ['relative_humidity', 'humidity', 'rh', 'RH']:
            if key in data_dict:
                data = data_dict[key]
                break
    
    if data is None:
        # 打印所有可用的键，帮助调试
        print(f"在{metadata_file}中找不到{variable}的数据，可用键: {list(data_dict.keys())}")
        raise ValueError(f"在{metadata_file}中找不到{variable}的数据")
    
    # 获取对应高度层和时间的数据
    pressure_idx = PRESSURE_LEVELS[pressure_level]
    time_idx = TIME_LEVELS[time_level]
    
    return data[time_idx, pressure_idx, :, :]

def get_all_dates(variable):
    """
    获取指定要素的所有可用日期（只在带_py的文件夹中查找）
    """
    data_dir = DATA_PATHS[variable]
    dates = []
    
    print(f"正在{data_dir}中查找带_py的文件夹...")
    
    all_items = os.listdir(data_dir)
    print(f"找到{len(all_items)}个项目")
    
    # 提取日期
    import re
    for item in all_items:
        if '_py' in item and os.path.isdir(os.path.join(data_dir, item)):
            date_matches = re.findall(r'\d{8}', item)
            if date_matches:
                date_str = date_matches[0]
                year = int(date_str[:4])
                if 2000 <= year <= 2024:
                    dates.append(date_str)
    
    print(f"最终找到{len(dates)}个可用日期")
    return sorted(dates)

def load_date_matrix(variable, date, pressure_level, time_level):
    """
    加载单个日期的矩阵数据
    """
    try:
        return date, load_matrix(variable, date, pressure_level, time_level)
    except Exception as e:
        print(f"处理日期{date}时出错: {e}")
        return date, None

def batch_process_dates(args):
    """
    批量处理日期的相似度计算
    """
    variable, dates, pressure_level, time_level, input_flat, batch_idx, total_batches = args
    results = []
    batch_size = len(dates)
    
    print(f"批次 {batch_idx+1}/{total_batches} 开始处理 {batch_size} 个日期")
    
    for i, date in enumerate(dates):
        try:
            # 加载当前日期的数据
            current_matrix = load_matrix(variable, date, pressure_level, time_level)
            # 计算相似度
            current_flat = current_matrix.flatten()
            distance = np.linalg.norm(input_flat - current_flat)
            results.append((date, pressure_level, time_level, distance))
            
            # 打印批次内进度
            if (i+1) % 50 == 0 or (i+1) == batch_size:
                print(f"批次 {batch_idx+1}/{total_batches}: 已处理 {i+1}/{batch_size} 个日期")
                
        except Exception as e:
            print(f"处理日期{date}时出错: {e}")
    
    print(f"批次 {batch_idx+1}/{total_batches} 处理完成，成功处理 {len(results)} 个日期")
    return results

def find_similar_patterns(variable, input_date, input_pressure_level, input_time_level, k):
    """
    查找与输入条件相似的topk模式（同一高度层、同一时间、跨日期）
    
    参数:
    - variable: 要素，可选值为'u_winds'、'v_winds'、'relative_humidity'
    - input_date: 输入日期，格式为'YYYYMMDD'
    - input_pressure_level: 输入高度层，可选值为'925hPa'、'500hPa'
    - input_time_level: 输入时间，可选值为'00UTC'、'06UTC'、'12UTC'、'18UTC'
    - k: 返回的相似模式数量
    
    返回:
    - 包含相似性排序的列表，每个元素包含(日期, 高度层, 时间, 距离)
    """
    print(f"开始分析相似性...")
    print(f"要素: {variable}")
    print(f"输入条件: {input_date} {input_time_level} {input_pressure_level}")
    
    # 加载输入条件的矩阵
    print(f"加载输入条件的矩阵...")
    input_matrix = load_matrix(variable, input_date, input_pressure_level, input_time_level)
    input_flat = input_matrix.flatten()
    
    # 获取所有可用日期
    print(f"获取所有可用日期...")
    all_dates = get_all_dates(variable)
    print(f"找到 {len(all_dates)} 个可用日期")
    
    # 过滤掉输入日期
    other_dates = [date for date in all_dates if date != input_date]
    total_comparisons = len(other_dates)
    print(f"需要计算 {total_comparisons} 个相似度")
    
    # 准备并行处理的参数
    print(f"准备并行处理...")
    
    # 使用全部CPU核心进行并行计算
    num_processes = cpu_count()
    print(f"使用 {num_processes} 个进程进行并行计算")
    
    # 平均分配日期到每个进程
    batch_size = max(1, len(other_dates) // num_processes)
    date_batches = [other_dates[i:i+batch_size] for i in range(0, len(other_dates), batch_size)]
    
    # 准备批次参数
    pool_args = []
    total_batches = len(date_batches)
    for i, batch in enumerate(date_batches):
        pool_args.append((variable, batch, input_pressure_level, input_time_level, input_flat, i, total_batches))
    
    # 并行计算相似度
    print(f"并行计算相似度...")
    similarities = []
    
    with Pool(processes=num_processes) as pool:
        # 并行处理所有批次
        batch_results = pool.map(batch_process_dates, pool_args)
        
        # 合并结果
        for result in batch_results:
            similarities.extend(result)
            print(f"已处理 {len(similarities)}/{total_comparisons} 个相似度计算")
    
    # 按相似度排序（距离越小越相似）
    print(f"排序相似度结果...")
    similarities.sort(key=lambda x: x[3])
    
    # 返回topk
    print(f"返回前 {k} 个相似模式")
    return similarities[:k]

def visualize_patterns(variable, input_date, input_pressure_level, input_time_level, similar_patterns):
    """
    可视化输入条件和相似模式
    """
    print(f"开始可视化结果...")
    
    # 加载输入条件的数据
    input_matrix = load_matrix(variable, input_date, input_pressure_level, input_time_level)
    
    # 加载相似模式的数据
    similar_matrices = []
    for date, pressure_level, time_level, _ in similar_patterns:
        matrix = load_matrix(variable, date, pressure_level, time_level)
        similar_matrices.append((date, pressure_level, time_level, matrix))
    
    # 计算总图数量
    total_plots = 1 + len(similar_matrices)
    rows = (total_plots + 2) // 3  # 每行最多3个图
    cols = min(3, total_plots)
    
    # 创建画布
    fig, axes = plt.subplots(rows, cols, figsize=(15, 5 * rows))
    
    # 确保axes是数组
    if total_plots == 1:
        axes = [axes]
    else:
        axes = axes.flatten()
    
    # 绘制输入条件的图
    im = axes[0].imshow(input_matrix, cmap='viridis')
    axes[0].set_title(f'Input: {variable} | {input_date} | {input_time_level} | {input_pressure_level}', fontsize=10)
    axes[0].set_xticks([])  # 移除x轴刻度
    axes[0].set_yticks([])  # 移除y轴刻度
    plt.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04)
    
    # 绘制相似模式的图
    for i, (date, pressure_level, time_level, matrix) in enumerate(similar_matrices, 1):
        if i < len(axes):
            im = axes[i].imshow(matrix, cmap='viridis')
            axes[i].set_title(f'Top {i}: {variable} | {date} | {time_level} | {pressure_level}', fontsize=10)
            axes[i].set_xticks([])  # 移除x轴刻度
            axes[i].set_yticks([])  # 移除y轴刻度
            plt.colorbar(im, ax=axes[i], fraction=0.046, pad=0.04)
    
    # 隐藏多余的子图
    for i in range(total_plots, len(axes)):
        axes[i].set_visible(False)
    
    # 调整布局
    plt.tight_layout()
    
    # 保存图像
    output_file = f"{variable}_{input_pressure_level}_{input_time_level}_{input_date}_similarity.png"
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"可视化结果保存至: {output_file}")
    return output_file

def save_results(variable, input_date, input_pressure_level, input_time_level, similar_patterns):
    """
    保存输入条件和相似模式的数据及相关信息
    """
    # 加载输入条件的数据
    input_matrix = load_matrix(variable, input_date, input_pressure_level, input_time_level)
    input_metadata_path = get_metadata_file_path(variable, input_date)
    
    if input_metadata_path:
        with open(input_metadata_path, 'rb') as f:
            input_metadata = pickle.load(f)
    else:
        input_metadata = {}
    
    # 加载相似模式的数据
    similar_data = []
    for date, pressure_level, time_level, distance in similar_patterns:
        matrix = load_matrix(variable, date, pressure_level, time_level)
        metadata_path = get_metadata_file_path(variable, date)
        
        if metadata_path:
            with open(metadata_path, 'rb') as f:
                metadata = pickle.load(f)
        else:
            metadata = {}
        
        similar_data.append({
            'date': date,
            'pressure_level': pressure_level,
            'time_level': time_level,
            'distance': distance,
            'matrix': matrix,
            'metadata': metadata
        })
    
    # 构建结果字典
    result = {
        'input': {
            'variable': variable,
            'pressure_level': input_pressure_level,
            'time_level': input_time_level,
            'date': input_date,
            'matrix': input_matrix,
            'metadata': input_metadata
        },
        'similar': similar_data
    }
    
    # 保存结果
    output_file = f"{variable}_{input_pressure_level}_{input_time_level}_{input_date}_similarity_results.npz"
    np.savez(output_file, **result)
    
    return output_file

def analyze_similarity(variable, input_pressure_level, input_time_level, input_date, k):
    """
    主函数：分析历史相似性并生成结果（跨高度层、跨时间、跨日期）
    
    参数:
    - variable: 要素，可选值为'u_winds'、'v_winds'、'relative_humidity'
    - input_pressure_level: 输入高度层，可选值为'925hPa'、'500hPa'
    - input_time_level: 输入时间，可选值为'00UTC'、'06UTC'、'12UTC'、'18UTC'
    - input_date: 输入日期，格式为'YYYYMMDD'
    - k: 返回的相似模式数量
    
    返回:
    - 包含相似性排序的列表
    - 可视化图像路径
    - 保存结果的文件路径
    """
    print(f"====================================")
    print(f"开始相似性分析任务")
    print(f"====================================")
    
    # 验证输入参数
    print(f"验证输入参数...")
    if variable not in DATA_PATHS:
        raise ValueError(f"无效的要素: {variable}")
    
    if input_pressure_level not in PRESSURE_LEVELS:
        raise ValueError(f"无效的高度层: {input_pressure_level}")
    
    if input_time_level not in TIME_LEVELS:
        raise ValueError(f"无效的时间: {input_time_level}")
    
    print(f"参数验证通过")
    
    # 查找相似模式
    print(f"\n步骤1: 查找相似模式")
    similar_patterns = find_similar_patterns(variable, input_date, input_pressure_level, input_time_level, k)
    
    # 可视化结果
    print(f"\n步骤2: 生成可视化结果")
    visualization_path = visualize_patterns(variable, input_date, input_pressure_level, input_time_level, similar_patterns)
    
    # 保存结果
    print(f"\n步骤3: 保存分析结果")
    save_path = save_results(variable, input_date, input_pressure_level, input_time_level, similar_patterns)
    
    # 格式化输出结果
    print(f"\n步骤4: 格式化结果")
    formatted_results = []
    for date, pressure_level, time_level, distance in similar_patterns:
        formatted_results.append({
            '要素': variable,
            '日期': date,
            '高度层': pressure_level,
            '时间': time_level,
            '相似度': 1 / (1 + distance)  # 转换为相似度分数（0-1）
        })
    
    print(f"\n====================================")
    print(f"分析任务完成")
    print(f"====================================")
    
    return formatted_results, visualization_path, save_path

if __name__ == "__main__":
    # 示例用法
    variable = 'relative_humidity'
    input_pressure_level = '925hPa'
    input_time_level = '00UTC'
    input_date = '20240629'
    k = 5
    
    results, viz_path, save_path = analyze_similarity(variable, input_pressure_level, input_time_level, input_date, k)
    
    print("相似性排序结果:")
    for i, result in enumerate(results, 1):
        print(f"{i}. 日期: {result['日期']}, 相似度: {result['相似度']:.4f}")
    
    print(f"\n可视化结果保存至: {viz_path}")
    print(f"结果数据保存至: {save_path}")
