import numpy as np 
# 1. 读取pickle文件
import pickle
with open('H:/u_winds_2000_2024_extracted/era5.u_component_of_wind.20000401_py/era5.u_component_of_wind.20000401_u.pkl', 'rb') as f:
    data_dict = pickle.load(f)

# 2. 访问数据
data = data_dict['data']          # 数据数组
lat = data_dict['latitude']       # 纬度
lon = data_dict['longitude']      # 经度
time = data_dict['valid_time_datetime']  # 时间
pressure = data_dict['pressure_level']  # 气压层

# 3. 数据访问示例
# 获取第一个时间点，第一个气压层的数据
data_slice = data[0, 0, :, :]
# 获取特定经纬度的数据
# 查找纬度30.0°N和经度104.0°E的索引
lat_idx = np.where(lat == 30.0)[0][0]
lon_idx = np.where(lon == 104.0)[0][0]
point_data = data[:, :, lat_idx, lon_idx]
print(point_data)
print(data_slice)
