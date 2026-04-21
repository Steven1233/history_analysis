import numpy as np

# 分析交集结果文件
def analyze_intersection_file(file_path):
    print(f"分析文件: {file_path}")
    
    try:
        with np.load(file_path, allow_pickle=True) as data:
            # 打印文件包含的键
            print("\n文件包含的键:")
            for key in data.keys():
                print(f"  - {key}")
            
            # 打印各个键的值
            print("\n文件内容:")
            if 'input_date' in data:
                print(f"  输入日期: {data['input_date'].item()}")
            if 'pressure_level' in data:
                print(f"  高度层: {data['pressure_level'].item()}")
            if 'time_level' in data:
                print(f"  时间: {data['time_level'].item()}")
            if 'k' in data:
                print(f"  Topk: {data['k'].item()}")
            if 'intersection_dates' in data:
                print(f"  交集日期: {data['intersection_dates'].tolist()}")
                print(f"  交集日期数量: {len(data['intersection_dates'])}")
            
    except Exception as e:
        print(f"分析文件时出错: {e}")

if __name__ == "__main__":
    # 分析指定的交集结果文件
    file_path = "intersection_925hPa_00UTC_20240628_results.npz"
    analyze_intersection_file(file_path)
