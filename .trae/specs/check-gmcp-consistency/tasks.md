# Tasks

- [x] Task 1: 创建 check_gmcp_consistency.py 脚本主体框架
  - [x] SubTask 1.1: 实现命令行参数解析（--path, --workers, --output, --verbose）
  - [x] SubTask 1.2: 实现基准结构提取函数 extract_baseline(path)
  - [x] SubTask 1.3: 实现单文件检查函数 check_single_file(filepath, baseline)，返回结构差异和异常数据信息
  - [x] SubTask 1.4: 实现多进程并行扫描逻辑，使用 multiprocessing.Pool
  - [x] SubTask 1.5: 实现结果汇总与输出（控制台 + CSV）
- [x] Task 2: 验证脚本可正常运行
  - [x] SubTask 2.1: 用少量文件测试脚本基本功能
  - [x] SubTask 2.2: 确认输出格式正确

# Task Dependencies
- Task 2 depends on Task 1
