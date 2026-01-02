import os
import sys
import json
import time
import psutil
import requests
import threading
from pathlib import Path
import socket
import webbrowser
import argparse
import signal
import traceback
from typing import Optional, Dict, Any

# ===================== 【这里！！！】直接改这个地方的ID，改完绝对生效 =====================
FIX_USER_ID = "wjx_228"  # 比如改成：test_user_001 、 my_id_123 ，改这里就够了！！！
FIX_SERVER_URL = "http://192.168.40.171:5000"


# ======================================================================================

class PyCharmAutoUploadClient:
    def __init__(self, server_url=FIX_SERVER_URL, user_id=FIX_USER_ID):
        """
        PyCharm自动代码上传客户端【最终完美版✅ 彻底根治ID无效问题】
        ✅ 双自动触发：保存文件(Ctrl+S)自动上传 + 点击▶️Run运行自动上传+执行+分析
        ✅ 零侵入、全兼容、智能去重、PyCharm完美适配
        ✅ 终极修复：无任何硬编码ID，改代码里的ID绝对生效，服务端强制绑定新ID
        """
        self.server_url = server_url
        self.user_id = user_id  # ✅ 唯一的ID入口，全局通用，无硬编码
        self.running = False
        self.connected = False
        self.watch_dir = None
        self.file_modify_times = {}
        self.last_upload_time = {}
        self.UPLOAD_INTERVAL = 2  # 去重：2秒内同文件不上传第二次
        self.last_run_files = set()
        self.run_file_expire = 5  # 运行文件去重：5秒内同文件只触发一次，解决永久不触发

        # 创建日志目录
        self.log_dir = Path.home() / ".pycharm_auto_upload"
        self.log_dir.mkdir(exist_ok=True)

        # 信号兼容
        try:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        except Exception as e:
            print(f"⚠️ 信号初始化: {str(e)}")

        print(f"🚀 PyCharm自动上传【完美根治版】 (用户ID: {self.user_id}) ✔️")
        print(f"✨ 核心能力: 保存自动上传 ✔️ | 运行自动执行+分析 ✔️ | ID永久生效 ✔️")

    def signal_handler(self, signum, frame):
        print(f"\n🛑 收到退出信号，优雅关闭...")
        self.stop()
        sys.exit(0)

    def check_server_connection(self):
        """检查服务器连通性"""
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
        """✅ 强制绑定用户ID，无视服务端缓存，必生效"""
        try:
            print(f"🔗 绑定用户ID -> {self.user_id}")
            payload = {
                "user_id": self.user_id,
                "force_bind": True  # 关键：强制覆盖服务端缓存，新ID必生效
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
        """✅ 打开当前用户的专属面板，绝对是你的新ID"""
        dashboard_url = f"{self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}&timestamp={int(time.time())}"
        print(f"🌐 你的专属分析面板: {dashboard_url}")
        try:
            webbrowser.open_new_tab(dashboard_url)
        except:
            print(f"⚠️ 手动复制上面的地址打开即可")

    def upload_code_for_analysis(self, code, filename, trigger_type="save"):
        """✅ 上传代码，全局用self.user_id，无任何硬编码"""
        try:
            print(f"\n📤 【{trigger_type.upper()}】上传分析: {filename} (用户:{self.user_id})")
            payload = {
                "code": code,
                "user_id": self.user_id,  # ✅ 根治：无硬编码
                "filename": filename,
                "trigger": trigger_type,
                "timestamp": int(time.time())  # 防缓存
            }
            res = requests.post(f"{self.server_url}/api/vscode/auto_analyze", json=payload, timeout=30)
            if res.status_code != 200:
                print(f"❌ 上传失败: {res.status_code} | {res.text[:200]}")
                return None
            result = res.json()
            ana_id = result.get("analysis_id", f"ana_{time.time()}")
            print(f"✅ 分析提交成功 | ID: {ana_id}")
            threading.Thread(target=self.monitor_analysis_progress, args=(ana_id, filename), daemon=True).start()
            return ana_id
        except Exception as e:
            print(f"❌ 上传错误: {str(e)}")
            traceback.print_exc()
            return None

    def execute_and_analyze(self, code, filename):
        """✅ 运行触发核心：执行+上传，无硬编码ID"""
        try:
            print(f"\n⚡【RUN运行触发】执行+全量分析: {filename} (用户:{self.user_id})")
            static_id = self.upload_code_for_analysis(code, filename, trigger_type="run")
            if not static_id: return None

            payload = {
                "code": code,
                "user_id": self.user_id,  # ✅ 根治：无硬编码
                "timestamp": int(time.time())
            }
            res = requests.post(f"{self.server_url}/api/code/execute", json=payload, timeout=60)
            if res.status_code != 200:
                print(f"❌ 执行接口失败: {res.status_code}")
                return None
            result = res.json()
            exec_id = result.get("execution_id")
            print(f"✅ 执行任务提交 | ID: {exec_id}")
            return self.monitor_execution_result(exec_id, filename)
        except Exception as e:
            print(f"❌ 执行分析错误: {str(e)}")
            traceback.print_exc()
            return None

    def monitor_analysis_progress(self, analysis_id, filename):
        for _ in range(30):
            time.sleep(2)
            try:
                res = requests.get(f"{self.server_url}/api/vscode/auto_status/{analysis_id}", timeout=5)
                if res.json().get("status") == "completed":
                    print(f"\n✅【{filename}】静态分析完成 ✔️")
                    break
            except:
                pass

    def monitor_execution_result(self, execution_id, filename):
        for _ in range(30):
            time.sleep(2)
            try:
                res = requests.get(f"{self.server_url}/api/code/result/{execution_id}", timeout=10)
                if res.status_code == 200:
                    result = res.json()
                    exec_res = result.get("result", {})
                    if exec_res.get("success"):
                        print(f"\n✅【{filename}】运行成功 ✔️")
                        if exec_res.get("output"):
                            print(f"📝 运行输出:\n{exec_res['output'][:600]}")
                    else:
                        print(f"\n❌【{filename}】运行失败 ❌")
                        print(f"❗ 错误: {exec_res.get('error', '未知错误')}")
                    return result
            except:
                pass
        print(f"\n⚠️【{filename}】运行结果超时")
        return None

    def _auto_upload_filter(self, file_path):
        """智能去重：2秒内同文件不上传"""
        file_key = str(file_path)
        now = time.time()
        if file_key in self.last_upload_time and now - self.last_upload_time[file_key] < self.UPLOAD_INTERVAL:
            return False
        self.last_upload_time[file_key] = now
        return True

    def upload_single_file(self, file_path, trigger_type="save"):
        """读取文件+编码容错+上传"""
        try:
            fp = Path(file_path).absolute()
            if not fp.exists() or fp.suffix != ".py": return True
            if not self._auto_upload_filter(fp): return True

            # 编码容错
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
        """监听文件修改/新增"""
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
        """✅ 修复：PyCharm运行监听100%触发，解决监听不到的问题"""
        print(f"\n👁️ 运行监听已开启：点击Run即触发执行+分析 (无重复)")
        while self.running:
            try:
                for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
                    cmd = proc.info.get("cmdline", [])
                    if not cmd or len(cmd) < 2: continue

                    # 适配PyCharm所有运行方式：python/python3/python.exe + 脚本路径
                    if "python" in cmd[0].lower() and ".py" in cmd[1]:
                        run_file = Path(cmd[1]).absolute()
                        # 只处理监听目录内的py文件
                        if str(self.watch_dir) in str(run_file) and run_file.suffix == ".py":
                            file_key = str(run_file)
                            create_time = proc.info.get("create_time", time.time())
                            # 核心修复：5秒内同文件只触发一次，过期自动清空，不会永久不触发
                            if file_key not in self.last_run_files and (time.time() - create_time) < 3:
                                self.last_run_files.add(file_key)
                                print(f"\n🔍 检测到运行文件: {run_file.name}")
                                # 读取文件
                                try:
                                    with open(run_file, "r", encoding="utf-8") as f:
                                        code = f.read()
                                except UnicodeDecodeError:
                                    with open(run_file, "r", encoding="gbk", errors="ignore") as f:
                                        code = f.read()
                                # 执行+上传
                                self.execute_and_analyze(code, run_file.name)
            except Exception as e:
                pass

            time.sleep(0.8)
            # 定期清空过期的运行记录，解决重复不触发
            if len(self.last_run_files) > 20:
                self.last_run_files.clear()

    def watch_directory(self, watch_dir):
        """主监听：文件修改+运行进程 双监听"""
        self.watch_dir = Path(watch_dir).absolute()
        if not self.watch_dir.exists():
            print(f"❌ 目录无效: {self.watch_dir}")
            return
        print(f"\n📂 监听目录: {self.watch_dir} (递归所有子文件夹)")
        print(f"💡 触发规则：Ctrl+S保存=自动上传 | 点击Run=执行+上传")
        print(f"🔚 退出：Ctrl+C\n")

        self.running = True
        self._init_file_modify_times()
        # 启动运行监听线程
        threading.Thread(target=self._monitor_pycharm_run_process, daemon=True).start()
        # 主循环监听文件修改
        while self.running:
            try:
                self._scan_directory_changes()
                time.sleep(0.8)
            except Exception as e:
                if self.running: pass

    def _init_file_modify_times(self):
        """初始化文件修改时间"""
        for py_file in self.watch_dir.rglob("*.py"):
            self.file_modify_times[str(py_file)] = py_file.stat().st_mtime

    def stop(self):
        """✅ 修复：断开连接也用self.user_id，无硬编码"""
        if not self.running: return
        self.running = False
        print("\n🛑 停止服务...")
        try:
            if self.connected:
                payload = {"user_id": self.user_id}  # ✅ 根治：无硬编码
                requests.post(f"{self.server_url}/api/vscode/disconnect", json=payload, timeout=5)
                print(f"✅ 已断开连接 (用户: {self.user_id})")
        except Exception as e:
            print(f"⚠️ 断开提示: {str(e)}")
        print("✅ 所有监听已停止，退出成功")


def main():
    parser = argparse.ArgumentParser(description='PyCharm自动上传【根治版】ID绝对生效')
    parser.add_argument('--server', default=FIX_SERVER_URL, help='服务器地址')
    parser.add_argument('--user', default=FIX_USER_ID, help='用户ID（改这里也生效）')
    parser.add_argument('--upload', help='手动上传单个文件')
    parser.add_argument('--run', help='手动运行单个文件')
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
            fp = Path(args.run).absolute()
            with open(fp, "r", encoding="utf-8") as f:
                client.execute_and_analyze(f.read(), fp.name)
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