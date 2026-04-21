import numpy as np
import os
import pickle
import matplotlib.pyplot as plt
from datetime import datetime
from similarity_analysis import analyze_similarity

# 要素列表
VARIABLES = ['u_winds', 'v_winds', 'relative_humidity']

# 高度层和时间
PRESSURE_LEVEL = '925hPa'
TIME_LEVEL = '00UTC'

# 输入日期
INPUT_DATE = '20240526'

# Topk数量
K = 30

def get_similar_dates(variable, input_date, pressure_level, time_level, k):
    """
    获取指定要素的Topk相似日期
    """
    print(f"\n计算{variable}的相似日期...")
    results, _, _ = analyze_similarity(variable, pressure_level, time_level, input_date, k)
    # 提取日期列表
    similar_dates = [result['日期'] for result in results]
    print(f"{variable}的Top{k}相似日期: {similar_dates}")
    return similar_dates

def find_intersection(similar_dates_list):
    """
    查找多个要素相似日期的交集
    """
    if not similar_dates_list:
        return []
    
    # 取第一个要素的相似日期作为初始集合
    intersection = set(similar_dates_list[0])
    
    # 与其他要素的相似日期取交集
    for dates in similar_dates_list[1:]:
        intersection.intersection_update(set(dates))
    
    return sorted(list(intersection))

def visualize_intersection(input_date, intersection_dates, pressure_level, time_level):
    """
    可视化交集日期，包括指定日期的对比
    """
    if not intersection_dates:
        print("没有找到交集日期")
        return None
    
    print(f"\n找到{len(intersection_dates)}个交集日期: {intersection_dates}")
    
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei']  # 使用黑体
    plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
    
    # 总日期数（包括指定日期）
    total_dates = 1 + len(intersection_dates)
    
    # 为指定日期和每个交集日期加载所有要素的数据
    fig, axes = plt.subplots(total_dates, len(VARIABLES), figsize=(15, 5 * total_dates))
    
    # 绘制指定日期（输入日期）
    for j, variable in enumerate(VARIABLES):
        try:
            # 加载数据
            from similarity_analysis import load_matrix
            matrix = load_matrix(variable, input_date, pressure_level, time_level)
            
            # 绘制热力图
            ax = axes[0, j] if total_dates > 1 else axes[j]
            im = ax.imshow(matrix, cmap='viridis')
            ax.set_title(f'指定日期 | {variable} | {input_date} | {time_level} | {pressure_level}')
            ax.set_xticks([])
            ax.set_yticks([])
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        except Exception as e:
            print(f"加载{variable}在{input_date}的数据时出错: {e}")
            ax = axes[0, j] if total_dates > 1 else axes[j]
            ax.set_title(f'指定日期 | {variable} | {input_date} | 数据加载失败')
            ax.set_xticks([])
            ax.set_yticks([])
    
    # 绘制交集日期
    for i, date in enumerate(intersection_dates, 1):
        for j, variable in enumerate(VARIABLES):
            try:
                # 加载数据
                from similarity_analysis import load_matrix, analyze_similarity
                matrix = load_matrix(variable, date, pressure_level, time_level)
                
                # 获取该日期的相似性排名和相似度
                results, _, _ = analyze_similarity(variable, pressure_level, time_level, input_date, K)
                rank = None
                similarity = None
                for idx, result in enumerate(results, 1):
                    if result['日期'] == date:
                        rank = idx
                        similarity = result['相似度']
                        break
                
                # 绘制热力图
                ax = axes[i, j]
                im = ax.imshow(matrix, cmap='viridis')
                # 合理排布标题，避免文字重叠
                if rank is not None and similarity is not None:
                    title_lines = [
                        f'交集日期',
                        f'排名: {rank}',
                        f'相似度: {similarity:.4f}',
                        f'{variable}',
                        f'{date} {time_level}',
                        f'{pressure_level}'
                    ]
                else:
                    title_lines = [
                        f'交集日期',
                        f'{variable}',
                        f'{date} {time_level}',
                        f'{pressure_level}'
                    ]
                ax.set_title('\n'.join(title_lines), fontsize=9)
                ax.set_xticks([])
                ax.set_yticks([])
                plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            except Exception as e:
                print(f"加载{variable}在{date}的数据时出错: {e}")
                ax = axes[i, j]
                ax.set_title(f'交集日期 | {variable} | {date} | 数据加载失败')
                ax.set_xticks([])
                ax.set_yticks([])
    
    # 调整布局
    plt.tight_layout()
    
    # 保存图像
    output_file = f"intersection_{pressure_level}_{time_level}_{INPUT_DATE}.png"
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"可视化结果保存至: {output_file}")
    return output_file

def regenerate_visualization_from_results(results_file):
    """
    基于已有的结果文件重新生成可视化
    """
    print(f"\n从结果文件 {results_file} 重新生成可视化...")
    
    # 加载结果文件
    try:
        with np.load(results_file, allow_pickle=True) as data:
            input_date = data['input_date'].item()
            pressure_level = data['pressure_level'].item()
            time_level = data['time_level'].item()
            intersection_dates = data['intersection_dates'].tolist()
        
        print(f"加载结果成功:")
        print(f"  输入日期: {input_date}")
        print(f"  高度层: {pressure_level}")
        print(f"  时间: {time_level}")
        print(f"  交集日期: {intersection_dates}")
        
        # 重新生成可视化
        return visualize_intersection(input_date, intersection_dates, pressure_level, time_level)
        
    except Exception as e:
        print(f"加载结果文件时出错: {e}")
        return None

def save_intersection_results(input_date, intersection_dates, pressure_level, time_level):
    """
    保存交集结果
    """
    result = {
        'input_date': input_date,
        'pressure_level': pressure_level,
        'time_level': time_level,
        'k': K,
        'intersection_dates': intersection_dates
    }
    
    # 保存结果
    output_file = f"intersection_{pressure_level}_{time_level}_{INPUT_DATE}_results.npz"
    np.savez(output_file, **result)
    
    print(f"结果数据保存至: {output_file}")
    return output_file

def main():
    """
    主函数
    """
    print("====================================")
    print("开始计算各个要素的相似日期交集")
    print("====================================")
    
    # 检查是否存在结果文件
    results_file = f"intersection_{PRESSURE_LEVEL}_{TIME_LEVEL}_{INPUT_DATE}_results.npz"
    
    # 询问用户是否基于已有结果文件重新生成可视化
    user_input = input(f"是否基于已有结果文件 {results_file} 重新生成可视化？(y/n): ")
    
    if user_input.lower() == 'y' and os.path.exists(results_file):
        # 基于已有结果文件重新生成可视化
        viz_path = regenerate_visualization_from_results(results_file)
    else:
        # 重新计算
        print(f"输入日期: {INPUT_DATE}")
        print(f"高度层: {PRESSURE_LEVEL}")
        print(f"时间: {TIME_LEVEL}")
        print(f"Topk: {K}")
        
        # 获取各个要素的相似日期
        similar_dates_list = []
        for variable in VARIABLES:
            similar_dates = get_similar_dates(variable, INPUT_DATE, PRESSURE_LEVEL, TIME_LEVEL, K)
            similar_dates_list.append(similar_dates)
        
        # 查找交集
        print("\n查找交集日期...")
        intersection_dates = find_intersection(similar_dates_list)
        
        if intersection_dates:
            print(f"找到{len(intersection_dates)}个交集日期: {intersection_dates}")
            
            # 可视化交集
            viz_path = visualize_intersection(INPUT_DATE, intersection_dates, PRESSURE_LEVEL, TIME_LEVEL)
            
            # 保存结果
            save_path = save_intersection_results(INPUT_DATE, intersection_dates, PRESSURE_LEVEL, TIME_LEVEL)
        else:
            print("没有找到交集日期")
    
    print("\n====================================")
    print("计算完成")
    print("====================================")

if __name__ == "__main__":
    main()
