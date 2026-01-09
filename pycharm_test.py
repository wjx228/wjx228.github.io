import sys
import json
import time
import psutil
import requests
import threading
import subprocess  # 新增：用于运行py文件
from pathlib import Path
import socket
import webbrowser
import argparse
import signal
import traceback
from typing import Optional, Dict, Any

# ===================== 配置项 =====================
FIX_USER_ID = "stu1"
FIX_SERVER_URL = "http://192.168.40.171:5000"

# ======================================================================================

class PyCharmAutoUploadClient:
    def __init__(self, server_url=FIX_SERVER_URL, user_id=FIX_USER_ID):
        self.server_url = server_url
        self.user_id = user_id
        self.running = False
        self.connected = False
        self.watch_dir = None
        self.file_modify_times = {}
        self.last_upload_time = {}
        self.UPLOAD_INTERVAL = 2
        self.last_run_files = set()
        self.run_file_expire = 5

        # 创建日志目录
        self.log_dir = Path.home() / ".pycharm_auto_upload"
        self.log_dir.mkdir(exist_ok=True)

        # 信号兼容
        try:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        except Exception as e:
            print(f"⚠️ 信号初始化: {str(e)}")

        print(f"🚀 PyCharm自动上传【运行版】 (用户ID: {self.user_id}) ✔️")
        print(f"✨ 核心能力: 上传分析 ✔️ | 运行文件 ✔️ | ID永久生效 ✔️")

    def start(self):
        print("\n📌 启动默认模式：监听当前目录下所有.py文件")
        if self.check_server_connection() and self.connect_to_server():
            self.watch_directory(Path.cwd())

    def signal_handler(self, signum, frame):
        print(f"\n🛑 收到退出信号，优雅关闭...")
        self.stop()
        sys.exit(0)

    def check_server_connection(self):
        try:
            res = requests.get(f"{self.server_url}/api/health", timeout=5)
            if res.status_code == 200:
                print(f"✅ 服务器连接成功: {self.server_url}")
                return True
            print(f"⚠️ 服务器响应异常: {res.status_code}")
            return True
        except Exception as e:
            print(f"❌ 服务器连接失败: {str(e)} | 请先启动服务端")
            return False

    def connect_to_server(self):
        try:
            print(f"🔗 绑定用户ID -> {self.user_id}")
            payload = {
                "user_id": self.user_id,
                "force_bind": True
            }
            res = requests.post(f"{self.server_url}/api/vscode/connect", json=payload, timeout=10)
            self.connected = True
            print(f"✅ 绑定成功！当前用户: {self.user_id} (永久生效)")
            self.open_dashboard()
            return True
        except Exception as e:
            print(f"⚠️ 绑定提示: {str(e)} | 不影响使用，ID={self.user_id}")
            self.connected = True
            self.open_dashboard()
            return True

    def open_dashboard(self):
        dashboard_url = f"{self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}&timestamp={int(time.time())}"
        print(f"🌐 你的专属分析面板: {dashboard_url}")
        try:
            webbrowser.open_new_tab(dashboard_url)
        except:
            print(f"⚠️ 手动复制上面的地址打开即可")

    def upload_code_for_analysis(self, code, filename, trigger_type="save"):
        """仅上传代码到服务端分析（保留原有逻辑）"""
        try:
            print(f"\n📤 【{trigger_type.upper()}】上传分析: {filename} (用户:{self.user_id})")
            payload = {
                "code": code,
                "user_id": self.user_id,
                "filename": filename,
                "trigger": trigger_type,
                "timestamp": int(time.time())
            }
            res = requests.post(f"{self.server_url}/api/vscode/auto_analyze", json=payload, timeout=30)

            if res.status_code not in [200, 202]:
                print(f"❌ 上传失败: {res.status_code} | {res.text[:200]}")
                return None

            result = res.json()
            ana_id = result.get("analysis_id", f"ana_{time.time()}")
            message = result.get("message", "代码已上传，AI分析中...")
            print(f"✅ 上传成功 | {message} | 分析ID: {ana_id}")

            threading.Thread(target=self.monitor_analysis_progress, args=(ana_id, filename), daemon=True).start()
            return ana_id
        except Exception as e:
            print(f"❌ 上传错误: {str(e)}")
            traceback.print_exc()
            return None

    def monitor_analysis_progress(self, analysis_id, filename):
        print(f"⌛ 等待【{filename}】AI分析完成 (分析ID: {analysis_id})")
        for _ in range(60):
            time.sleep(2)
            try:
                res = requests.get(f"{self.server_url}/api/vscode/auto_status/{analysis_id}", timeout=5)
                if res.status_code == 200:
                    status_data = res.json()
                    if status_data.get("status") == "completed":
                        print(f"\n✅【{filename}】静态分析完成 ✔️")
                        if status_data.get("result"):
                            print(f"📊 分析结果: {status_data['result'].get('summary', '分析完成')}")
                        break
                    elif status_data.get("status") == "processing":
                        print(f"🔄 【{filename}】分析中... (进度: {status_data.get('progress', '未知')})")
                    else:
                        print(f"⚠️ 【{filename}】分析状态: {status_data.get('status', '未知')}")
            except Exception as e:
                pass

        print(f"\n📌 【{filename}】分析监听结束 (如需查看结果，可打开专属面板)")

    # ========== 核心改动1：新增运行py文件的方法 ==========
    def run_file_locally(self, file_path):
        """运行py文件（替代服务端运行）"""
        try:
            file_path = Path(file_path).absolute()
            print(f"\n▶️ 开始运行: {file_path.name}")

            # 用subprocess执行py文件，捕获输出和错误
            result = subprocess.run(
                [sys.executable, str(file_path)],  # 使用当前Python解释器运行
                stdout=subprocess.PIPE,            # 捕获标准输出
                stderr=subprocess.PIPE,            # 捕获标准错误
                encoding="utf-8",                  # 编码统一为utf-8
                timeout=300                        # 超时时间5分钟（可调整）
            )

            # 输出运行结果
            if result.returncode == 0:  # 返回码0表示运行成功
                print(f"\n✅【{file_path.name}】运行成功 ✔️")
                if result.stdout:
                    print(f"📝 运行输出:\n{result.stdout}")
            else:  # 返回码非0表示运行失败
                print(f"\n❌【{file_path.name}】运行失败 ❌")
                if result.stderr:
                    print(f"❗ 错误信息:\n{result.stderr}")

            return result
        except subprocess.TimeoutExpired:
            print(f"\n❌【{file_path.name}】运行超时（5分钟）")
            return None
        except Exception as e:
            print(f"\n❌【{file_path.name}】运行异常: {str(e)}")
            traceback.print_exc()
            return None

    # ========== 核心改动2：修改execute_and_analyze方法 ==========
    def execute_and_analyze(self, code, filename):
        """上传分析 + 运行（移除服务端执行逻辑）"""
        try:
            print(f"\n⚡【RUN触发】上传分析 + 运行: {filename} (用户:{self.user_id})")
            # 第一步：上传代码到服务端分析
            static_id = self.upload_code_for_analysis(code, filename, trigger_type="run")
            if not static_id:
                return None

            # 第二步：运行该文件（核心改动）
            file_path = Path(filename).absolute()
            self.run_file_locally(file_path)

            return static_id  # 返回分析ID（不再返回执行ID）
        except Exception as e:
            print(f"❌ 执行分析错误: {str(e)}")
            traceback.print_exc()
            return None

    # ========== 移除原monitor_execution_result方法（无需监听服务端执行） ==========
    # （如果保留该方法也不影响，因为已不再调用）

    def _auto_upload_filter(self, file_path):
        file_key = str(file_path)
        now = time.time()
        if file_key in self.last_upload_time and now - self.last_upload_time[file_key] < self.UPLOAD_INTERVAL:
            return False
        self.last_upload_time[file_key] = now
        return True

    def upload_single_file(self, file_path, trigger_type="save"):
        try:
            fp = Path(file_path).absolute()
            if not fp.exists() or fp.suffix != ".py":
                return True
            if not self._auto_upload_filter(fp):
                return True

            try:
                with open(fp, "r", encoding="utf-8") as f:
                    code = f.read()
            except UnicodeDecodeError:
                with open(fp, "r", encoding="gbk", errors="ignore") as f:
                    code = f.read()

            self.upload_code_for_analysis(code, fp.name, trigger_type)
            return True
        except Exception as e:
            print(f"❌ 文件上传错误: {str(e)}")
            return False

    def _scan_directory_changes(self):
        current_files = {}
        for py_file in self.watch_dir.rglob("*.py"):
            if py_file.is_file():
                current_files[str(py_file)] = py_file.stat().st_mtime
                file_key = str(py_file)
                if file_key not in self.file_modify_times:
                    print(f"\n🆕 新增文件: {py_file.name}")
                    self.upload_single_file(py_file)
                elif current_files[file_key] > self.file_modify_times[file_key] + 0.5:
                    print(f"\n✏️ 修改文件: {py_file.name}")
                    self.upload_single_file(py_file)
        self.file_modify_times = current_files

    def _monitor_pycharm_run_process(self):
        print(f"\n👁️ 运行监听已开启：点击Run即触发上传+运行 (无重复)")
        while self.running:
            try:
                for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
                    cmd = proc.info.get("cmdline", [])
                    if not cmd or len(cmd) < 2:
                        continue

                    if "python" in cmd[0].lower() and ".py" in cmd[1]:
                        run_file = Path(cmd[1]).absolute()
                        if str(self.watch_dir) in str(run_file) and run_file.suffix == ".py":
                            file_key = str(run_file)
                            create_time = proc.info.get("create_time", time.time())
                            if file_key not in self.last_run_files and (time.time() - create_time) < 3:
                                self.last_run_files.add(file_key)
                                print(f"\n🔍 检测到运行文件: {run_file.name}")
                                try:
                                    with open(run_file, "r", encoding="utf-8") as f:
                                        code = f.read()
                                    # 调用修改后的execute_and_analyze（上传+运行）
                                    self.execute_and_analyze(code, run_file.name)
                                except UnicodeDecodeError:
                                    with open(run_file, "r", encoding="gbk", errors="ignore") as f:
                                        code = f.read()
                                    self.execute_and_analyze(code, run_file.name)
            except Exception as e:
                pass

            time.sleep(0.8)
            if len(self.last_run_files) > 20:
                self.last_run_files.clear()

    def watch_directory(self, watch_dir):
        if isinstance(watch_dir, str):
            self.watch_dir = Path(watch_dir).absolute()
        else:
            self.watch_dir = watch_dir.absolute()

        if not self.watch_dir.exists():
            print(f"❌ 目录无效: {self.watch_dir}")
            return
        print(f"\n📂 监听目录: {self.watch_dir} (递归所有子文件夹)")
        print(f"💡 触发规则：Ctrl+S保存=自动上传 | 点击Run=上传+运行")
        print(f"🔚 退出：Ctrl+C\n")

        self.running = True
        self._init_file_modify_times()
        threading.Thread(target=self._monitor_pycharm_run_process, daemon=True).start()
        while self.running:
            try:
                self._scan_directory_changes()
                time.sleep(0.8)
            except Exception as e:
                if self.running:
                    pass

    def _init_file_modify_times(self):
        for py_file in self.watch_dir.rglob("*.py"):
            self.file_modify_times[str(py_file)] = py_file.stat().st_mtime

    def stop(self):
        if not self.running:
            return
        self.running = False
        print("\n🛑 停止服务...")
        try:
            if self.connected:
                payload = {"user_id": self.user_id}
                requests.post(f"{self.server_url}/api/vscode/disconnect", json=payload, timeout=5)
                print(f"✅ 已断开连接 (用户: {self.user_id})")
        except Exception as e:
            print(f"⚠️ 断开提示: {str(e)}")
        print("✅ 所有监听已停止，退出成功")


def main():
    parser = argparse.ArgumentParser(description='PyCharm自动上传【运行版】ID绝对生效')
    parser.add_argument('--server', default=FIX_SERVER_URL, help='服务器地址')
    parser.add_argument('--user', default=FIX_USER_ID, help='用户ID（改这里也生效）')
    parser.add_argument('--upload', help='手动上传单个文件')
    parser.add_argument('--run', help='手动上传+运行单个文件')  # 注释更新
    parser.add_argument('--watch', help='监听目录【核心】')
    args = parser.parse_args()

    client = PyCharmAutoUploadClient(server_url=args.server, user_id=args.user)
    try:
        if args.upload:
            client.check_server_connection()
            client.connect_to_server()
            client.upload_single_file(args.upload)
            client.stop()
        elif args.run:
            client.check_server_connection()
            client.connect_to_server()
            # 调用修改后的逻辑：先上传分析，再运行
            fp = Path(args.run).absolute()
            with open(fp, "r", encoding="utf-8") as f:
                code = f.read()
            client.execute_and_analyze(code, fp.name)
            client.stop()
        elif args.watch:
            if client.check_server_connection() and client.connect_to_server():
                client.watch_directory(args.watch)
        else:
            client.start()
    except KeyboardInterrupt:
        print("\n🛑 用户手动中断")
        client.stop()
    except Exception as e:
        print(f"❌ 运行错误: {str(e)}")
        traceback.print_exc()
        client.stop()
    print(f"\n👋 感谢使用 (用户ID: {client.user_id})")


if __name__ == "__main__":
    main()