import numpy as np
import os
import matplotlib.pyplot as plt

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei']  # 使用黑体
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

def visualize_saved_results(results_file):
    """
    可视化已保存的结果文件，支持similarity_results.npz和intersection_*_results.npz格式
    """
    print(f"\n可视化已保存的结果文件: {results_file}")
    
    # 加载结果文件
    try:
        with np.load(results_file, allow_pickle=True) as data:
            # 检查文件结构
            print(f"文件包含的键: {list(data.keys())}")
            
            # 检查是否是交集结果文件
            if 'intersection_dates' in data:
                # 处理交集结果文件
                input_date = data['input_date'].item()
                pressure_level = data['pressure_level'].item()
                time_level = data['time_level'].item()
                k = data['k'].item()
                intersection_dates = data['intersection_dates'].tolist()
                
                print(f"加载交集结果成功:")
                print(f"  输入日期: {input_date}")
                print(f"  高度层: {pressure_level}")
                print(f"  时间: {time_level}")
                print(f"  Topk: {k}")
                print(f"  交集日期: {intersection_dates}")
                
                # 要素列表
                VARIABLES = ['u_winds', 'v_winds', 'relative_humidity']
                
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
                        title_lines = [
                            f'指定日期',
                            f'{variable}',
                            f'{input_date} {time_level}',
                            f'{pressure_level}'
                        ]
                        ax.set_title('\n'.join(title_lines), fontsize=9)
                        ax.set_xticks([])
                        ax.set_yticks([])
                        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                    except Exception as e:
                        print(f"加载{variable}在{input_date}的数据时出错: {e}")
                        ax = axes[0, j] if total_dates > 1 else axes[j]
                        title_lines = [
                            f'指定日期',
                            f'{variable}',
                            f'{input_date}',
                            f'数据加载失败'
                        ]
                        ax.set_title('\n'.join(title_lines), fontsize=9)
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
                            results, _, _ = analyze_similarity(variable, pressure_level, time_level, input_date, k)
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
                            title_lines = [
                                f'交集日期',
                                f'{variable}',
                                f'{date}',
                                f'数据加载失败'
                            ]
                            ax.set_title('\n'.join(title_lines), fontsize=9)
                            ax.set_xticks([])
                            ax.set_yticks([])
                
                # 调整布局
                plt.tight_layout()
                
                # 生成输出文件名
                output_file = os.path.splitext(results_file)[0] + '.png'
                plt.savefig(output_file, dpi=150, bbox_inches='tight')
                plt.close()
                
                print(f"可视化结果保存至: {output_file}")
                return output_file
            
            # 尝试不同的键名结构（相似性结果文件）
            elif 'input' in data:
                input_data = data['input'].item()
                similar_data = data['similar'].tolist()
            elif 'input_data' in data:
                input_data = data['input_data'].item()
                similar_data = data['similar_data'].tolist()
            else:
                print("无法识别文件结构")
                return None
            
        print(f"加载相似性结果成功:")
        print(f"  要素: {input_data.get('variable', '未知')}")
        print(f"  输入日期: {input_data.get('date', '未知')}")
        print(f"  高度层: {input_data.get('pressure_level', '未知')}")
        print(f"  时间: {input_data.get('time_level', '未知')}")
        print(f"  相似模式数量: {len(similar_data)}")
        
        # 提取输入数据
        input_matrix = input_data.get('matrix')
        input_date = input_data.get('date', '未知')
        input_pressure = input_data.get('pressure_level', '未知')
        input_time = input_data.get('time_level', '未知')
        variable = input_data.get('variable', '未知')
        
        # 提取相似数据
        similar_matrices = []
        similar_info = []
        for item in similar_data:
            similar_matrices.append(item.get('matrix'))
            similar_info.append({
                'date': item.get('date', '未知'),
                'pressure_level': item.get('pressure_level', '未知'),
                'time_level': item.get('time_level', '未知'),
                'distance': item.get('distance', '未知'),
                'similarity': 1 / (1 + item.get('distance', 1)) if 'distance' in item else '未知'
            })
        
        # 计算总图数量
        total_plots = 1 + len(similar_matrices)
        rows = (total_plots + 2) // 3  # 每行最多3个图
        cols = min(3, total_plots)
        
        # 创建画布
        fig, axes = plt.subplots(rows, cols, figsize=(15, 5 * rows))
        axes = axes.flatten() if total_plots > 1 else [axes]
        
        # 绘制输入条件的图
        if input_matrix is not None:
            im = axes[0].imshow(input_matrix, cmap='viridis')
            title_lines = [
                f'输入条件',
                f'{variable}',
                f'{input_date} {input_time}',
                f'{input_pressure}'
            ]
            axes[0].set_title('\n'.join(title_lines), fontsize=9)
            axes[0].set_xticks([])
            axes[0].set_yticks([])
            plt.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04)
        else:
            axes[0].set_title('输入数据加载失败')
            axes[0].set_xticks([])
            axes[0].set_yticks([])
        
        # 绘制相似模式的图
        for i, (matrix, info) in enumerate(zip(similar_matrices, similar_info), 1):
            if i < len(axes):
                if matrix is not None:
                    im = axes[i].imshow(matrix, cmap='viridis')
                    title_lines = [
                        f'Top {i}',
                        f'相似度: {info.get("similarity", "未知"):.4f}',
                        f'{variable}',
                        f'{info.get("date", "未知")} {info.get("time_level", "未知")}',
                        f'{info.get("pressure_level", "未知")}'
                    ]
                    axes[i].set_title('\n'.join(title_lines), fontsize=9)
                    axes[i].set_xticks([])
                    axes[i].set_yticks([])
                    plt.colorbar(im, ax=axes[i], fraction=0.046, pad=0.04)
                else:
                    axes[i].set_title(f'Top {i}: 数据加载失败')
                    axes[i].set_xticks([])
                    axes[i].set_yticks([])
        
        # 隐藏多余的子图
        for i in range(total_plots, len(axes)):
            axes[i].set_visible(False)
        
        # 调整布局
        plt.tight_layout()
        
        # 生成输出文件名
        output_file = os.path.splitext(results_file)[0] + '_visualization.png'
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"可视化结果保存至: {output_file}")
        return output_file
        
    except Exception as e:
        print(f"加载结果文件时出错: {e}")
        return None

def main():
    """
    主函数
    """
    print("====================================")
    print("可视化已保存的相似性结果")
    print("====================================")
    
    # 询问用户输入结果文件路径
    results_file = input("请输入结果文件路径（例如: relative_humidity_925hPa_00UTC_20240628_similarity_results.npz）: ")
    
    if os.path.exists(results_file):
        visualize_saved_results(results_file)
    else:
        print(f"文件 {results_file} 不存在")
    
    print("\n====================================")
    print("可视化完成")
    print("====================================")

if __name__ == "__main__":
    main()
