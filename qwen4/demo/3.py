from pathlib import Path

# 替换成你的路径（用原始字符串）
target_path = Path(r"D:\wjx228.github.io\qwen4\demo")
print("路径是否存在：", target_path.exists())
print("是否有权限访问：", target_path.is_dir())  # 返回True说明有权限
print("目录下的文件：", list(target_path.glob("*")))  # 能列出文件说明正常