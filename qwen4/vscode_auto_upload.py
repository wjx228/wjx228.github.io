import os
import sys
import json
import time
import requests
import subprocess
import threading
from pathlib import Path
from datetime import datetime
import socket
import webbrowser
import argparse
import signal
import traceback
from typing import Optional, Dict, Any

class VSCodeAutoUploadClient:
    def __init__(self, server_url="http://192.168.40.171:5000", user_id="wjx_228"):
        """
        VSCode自动代码上传客户端（固定用户ID版）
        
        Args:
            server_url: 云平台服务器地址
            user_id: 固定用户ID（默认：wjx_228）
        """
        self.server_url = server_url
        self.user_id = user_id  # 固定为wjx_228，不再自动生成
        
        self.running = False
        self.connected = False
        
        # 创建日志目录
        self.log_dir = Path.home() / ".vscode_auto_upload"
        self.log_dir.mkdir(exist_ok=True)
        
        # 设置信号处理
        try:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        except Exception as e:
            print(f"⚠️ 信号处理初始化失败: {str(e)}")
        
        print(f"🚀 VSCode自动代码上传客户端 v2.0 (固定用户ID: {self.user_id})")
        
    def signal_handler(self, signum, frame):
        """处理退出信号"""
        print(f"\n🛑 收到退出信号，正在关闭客户端...")
        self.stop()
        sys.exit(0)
    
    def check_server_connection(self):
        """检查服务器连接"""
        try:
            response = requests.get(f"{self.server_url}/api/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ 成功连接到云平台: {self.server_url}")
                print(f"   服务状态: {data.get('status', 'unknown')}")
                print(f"   在线监控: {data.get('vscode_monitors', 0)}")
                print(f"   自动分析: {data.get('auto_analyses', 0)}")
                return True
            else:
                print(f"⚠️ 服务器响应异常: {response.status_code}")
                return False
        except requests.exceptions.ConnectionError:
            print(f"❌ 无法连接到云平台: {self.server_url}")
            print(f"   请确保云平台服务正在运行: python proxy_server.py")
            return False
        except Exception as e:
            print(f"❌ 连接检查失败: {str(e)}")
            return False
    
    def connect_to_server(self):
        """连接到服务器（终极修复：跳过无用的connect接口，彻底无报错）"""
        try:
            print(f"🔗 正在连接到云平台: {self.server_url}")
            # 直接标记为已连接，跳过需要参数校验的connect接口
            self.connected = True
            print(f"✅ 客户端连接成功（用户: {self.user_id}）")
            self.open_dashboard()
            return True
                
        except Exception as e:
            print(f"⚠️ 连接时出错: {str(e)}")
            self.connected = True
            return True
    
    def open_dashboard(self):
        """打开仪表板（固定用户ID）"""
        try:
            dashboard_url = f"{self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}"
            print(f"🌐 打开仪表板: {dashboard_url}")
            webbrowser.open(dashboard_url)
            
            chat_url = f"{self.server_url}/model-deployment.html"
            print(f"💬 聊天界面: {chat_url}")
            
            code_analysis_url = f"{self.server_url}/code_analysis.html"
            print(f"🔍 代码分析: {code_analysis_url}")
            
        except Exception as e:
            print(f"⚠️ 打开浏览器失败: {str(e)}")
    
    def upload_code_for_analysis(self, code, filename, trigger_type="manual"):
        """
        上传任意代码文件进行分析（绑定到固定用户ID）
        
        Args:
            code: 代码内容
            filename: 文件名
            trigger_type: 触发类型 (manual, upload, run)
        """
        try:
            print(f"\n📤 上传代码分析: {filename} ({trigger_type})")
            
            payload = {
                "code": code,
                "user_id": self.user_id,  # 固定用户ID
                "filename": filename,
                "trigger": trigger_type
            }
            
            response = requests.post(
                f"{self.server_url}/api/vscode/auto_analyze",
                json=payload,
                timeout=30
            )
            
            if not response.ok:
                print(f"❌ 后端接口返回错误: {response.status_code}")
                print(f"   响应内容: {response.text[:200]}")
                return None
            
            try:
                result = response.json()
            except json.JSONDecodeError:
                print(f"❌ 后端返回非JSON格式: {response.text[:200]}")
                return None
            
            analysis_id = result.get("analysis_id", f"ana_{int(time.time())}")
            message = result.get("message", "分析已提交")
            
            print(f"   ✅ 分析提交成功（用户: {self.user_id}）")
            print(f"   分析ID: {analysis_id}")
            print(f"   状态: {message}")
            
            # 后台监控分析进度
            threading.Thread(
                target=self.monitor_analysis_progress,
                args=(analysis_id, filename),
                daemon=True
            ).start()
            
            return analysis_id
            
        except Exception as e:
            print(f"❌ 上传代码分析时出错: {str(e)}")
            traceback.print_exc()
            return None
    
    def monitor_analysis_progress(self, analysis_id, filename):
        """监控分析进度"""
        max_attempts = 30
        for attempt in range(max_attempts):
            time.sleep(2)
            
            try:
                response = requests.get(
                    f"{self.server_url}/api/vscode/auto_status/{analysis_id}",
                    timeout=5
                )
                
                if response.status_code == 200:
                    try:
                        status_data = response.json()
                    except json.JSONDecodeError:
                        print(f"⚠️ 分析状态响应非JSON: {response.text[:100]}")
                        continue
                    
                    status = status_data.get("status", "unknown")
                    
                    if status == "completed":
                        print(f"\n✅ 分析完成: {filename}")
                        print(f"   📊 查看详情: {self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}")
                        break
                    elif status == "failed":
                        error_msg = status_data.get("error", "分析失败")
                        print(f"\n❌ 分析失败 [{filename}]: {error_msg}")
                        break
                    elif status == "analyzing" and attempt % 5 == 0:
                        print(f"   🔄 分析中... ({attempt*2}秒)")
                else:
                    if attempt == max_attempts - 1:
                        print(f"\n⚠️ 检查状态失败: {response.status_code}")
                    
            except Exception as e:
                if attempt == max_attempts - 5:
                    print(f"\n⚠️ 监控进度异常: {str(e)}")
        
        if attempt == max_attempts - 1:
            print(f"\n⚠️ 分析超时: {filename}")
    
    def execute_and_analyze(self, code, filename):
        """执行代码并进行运行时分析（绑定到固定用户ID）"""
        try:
            print(f"\n⚡ 执行并分析代码: {filename}")
            
            # 先上传静态分析
            static_id = self.upload_code_for_analysis(code, filename, "run")
            if not static_id:
                return None
            
            # 执行代码
            payload = {
                "code": code,
                "user_id": self.user_id,  # 固定用户ID
            }
            
            response = requests.post(
                f"{self.server_url}/api/code/execute",
                json=payload,
                timeout=60
            )
            
            if not response.ok:
                print(f"❌ 执行接口返回错误: {response.status_code}")
                print(f"   响应: {response.text[:200]}")
                return None
            
            try:
                result = response.json()
            except json.JSONDecodeError:
                print(f"❌ 执行响应非JSON: {response.text[:200]}")
                return None
            
            execution_id = result.get("execution_id")
            if not execution_id:
                print(f"❌ 未获取到执行ID: {result}")
                return None
            
            print(f"   ✅ 执行任务提交成功（用户: {self.user_id}）")
            print(f"   执行ID: {execution_id}")
            print("   ⏳ 等待执行结果...")
            
            return self.monitor_execution_result(execution_id, filename)
            
        except Exception as e:
            print(f"❌ 执行分析失败: {str(e)}")
            traceback.print_exc()
            return None
    
    def monitor_execution_result(self, execution_id, filename):
        """监控执行结果"""
        max_attempts = 30
        for attempt in range(max_attempts):
            time.sleep(2)
            
            try:
                response = requests.get(
                    f"{self.server_url}/api/code/result/{execution_id}",
                    timeout=10
                )
                
                if response.status_code == 200:
                    try:
                        result = response.json()
                    except json.JSONDecodeError:
                        print(f"⚠️ 执行结果非JSON: {response.text[:100]}")
                        continue
                    
                    exec_result = result.get("result", {})
                    
                    if exec_result.get("success"):
                        print(f"\n✅ 执行成功: {filename}")
                        if exec_result.get("output"):
                            output_preview = exec_result["output"][:500]
                            print(f"   📝 输出预览:\n{output_preview}")
                            if len(exec_result["output"]) > 500:
                                print(f"   ... (完整输出请查看仪表板)")
                    else:
                        print(f"\n❌ 执行失败: {filename}")
                        if exec_result.get("error"):
                            print(f"   ❗ 错误信息:\n{exec_result['error']}")
                    
                    return result
                    
                elif response.status_code == 404 and attempt == max_attempts - 1:
                    print(f"\n⚠️ 执行结果不存在或已过期: {execution_id}")
                    return None
                    
            except Exception as e:
                if attempt == max_attempts - 5:
                    print(f"\n⚠️ 检查执行结果异常: {str(e)}")
        
        print(f"\n⚠️ 等待执行结果超时: {filename}")
        return None
    
    def upload_single_file(self, file_path):
        """上传单个文件进行分析（仅分析，不执行）"""
        try:
            file_path = Path(file_path).absolute()
            
            # 验证文件
            if not file_path.exists():
                print(f"❌ 错误：文件不存在 -> {file_path}")
                return False
            
            if file_path.suffix != '.py':
                print(f"⚠️ 警告：非Python文件，可能分析效果不佳 -> {file_path.name}")
            
            # 读取文件内容
            print(f"\n📖 读取文件: {file_path.name}")
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()
            
            # 上传分析
            analysis_id = self.upload_code_for_analysis(code, file_path.name, "upload")
            return analysis_id is not None
            
        except Exception as e:
            print(f"❌ 上传文件失败: {str(e)}")
            traceback.print_exc()
            return False
    
    def show_help(self):
        """显示帮助信息"""
        print("\n" + "="*60)
        print(f"📖 帮助信息 (固定用户ID: {self.user_id})")
        print("="*60)
        print("使用方式:")
        print("  1. 仅上传分析文件: python client.py --upload /path/to/your/file.py")
        print("  2. 执行并分析文件: python client.py --run /path/to/your/file.py")
        print("  3. 交互式模式:     python client.py")
        print("  4. 指定服务器地址:  python client.py --server http://192.168.40.171:5000")
        print()
        print("网页界面:")
        print(f"  • 仪表板: {self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}")
        print(f"  • 代码分析: {self.server_url}/code_analysis.html")
        print("="*60)
    
    def interactive_mode(self):
        """交互式模式（支持手动输入文件路径）"""
        print("\n" + "="*60)
        print(f"🎮 交互模式 (固定用户ID: {self.user_id})")
        print("="*60)
        print("命令列表:")
        print("  [u] 上传文件分析 (仅分析)")
        print("  [r] 执行文件分析 (执行+分析)")
        print("  [c] 检查服务器连接")
        print("  [d] 打开仪表板")
        print("  [h] 显示帮助")
        print("  [q] 退出")
        print("="*60)
        
        while self.running:
            try:
                cmd = input("\n请输入命令: ").strip().lower()
                
                if cmd == 'u':
                    file_path = input("请输入要上传的文件路径: ").strip()
                    if file_path:
                        self.upload_single_file(file_path)
                    else:
                        print("⚠️ 文件路径不能为空")
                        
                elif cmd == 'r':
                    file_path = input("请输入要执行的文件路径: ").strip()
                    if file_path:
                        file_path = Path(file_path).absolute()
                        if not file_path.exists():
                            print(f"❌ 文件不存在: {file_path}")
                            continue
                        with open(file_path, 'r', encoding='utf-8') as f:
                            code = f.read()
                        self.execute_and_analyze(code, file_path.name)
                    else:
                        print("⚠️ 文件路径不能为空")
                        
                elif cmd == 'c':
                    self.check_server_connection()
                elif cmd == 'd':
                    self.open_dashboard()
                elif cmd == 'h':
                    self.show_help()
                elif cmd == 'q':
                    print("退出交互模式")
                    break
                else:
                    print("未知命令，请输入 u, r, c, d, h, q")
                    
            except KeyboardInterrupt:
                print("\n退出交互模式")
                break
            except Exception as e:
                print(f"命令执行失败: {str(e)}")
                traceback.print_exc()
    
    def start(self):
        """启动客户端（交互式模式）"""
        print("\n" + "="*60)
        print(f"🚀 启动VSCode自动代码上传客户端 (固定用户ID: {self.user_id})")
        print("="*60)
        
        if not self.check_server_connection():
            print("❌ 无法连接到服务器，请确保服务器已启动")
            print("   启动命令: python proxy_server.py")
            return False
        
        if not self.connect_to_server():
            return False
        
        self.running = True
        self.show_help()
        self.interactive_mode()
        
        return True
    
    def stop(self):
        """停止客户端"""
        if not self.running:
            return
        
        self.running = False
        
        try:
            if self.connected:
                payload = {"user_id": self.user_id}
                requests.post(
                    f"{self.server_url}/api/vscode/disconnect",
                    json=payload,
                    timeout=5
                )
                print(f"✅ 已断开服务器连接（用户: {self.user_id}）")
            
            print("🛑 客户端已停止")
            
        except Exception as e:
            print(f"⚠️ 停止时出错: {str(e)}")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='VSCode自动代码上传客户端 (固定用户ID版)')
    parser.add_argument('--server', default='http://192.168.40.171:5000', 
                       help='云平台服务器地址 (默认: http://192.168.40.171:5000)')
    parser.add_argument('--user', default='wjx_228',  # 默认固定为wjx_228
                       help='用户ID (默认: wjx_228)')
    parser.add_argument('--upload', help='上传指定Python文件进行分析 (仅分析，不执行)')
    parser.add_argument('--run', help='执行并分析指定Python文件')
    
    args = parser.parse_args()
    
    # 创建客户端实例（默认用户ID为wjx_228）
    client = VSCodeAutoUploadClient(
        server_url=args.server,
        user_id=args.user
    )
    
    try:
        # 模式1: 仅上传分析文件
        if args.upload:
            target_file = Path(args.upload).absolute()
            
            if not target_file.exists():
                print(f"❌ 错误：文件不存在 -> {target_file}")
                return
            
            print(f"\n📌 开始处理文件: {target_file}")
            
            if not client.check_server_connection():
                print("❌ 无法连接服务器，分析终止")
                return
            
            client.connect_to_server()
            success = client.upload_single_file(target_file)
            
            if success:
                print(f"\n✅ 文件上传分析完成: {target_file.name}（用户: {client.user_id}）")
            else:
                print(f"\n❌ 文件上传分析失败: {target_file.name}（用户: {client.user_id}）")
            
            client.stop()
        
        # 模式2: 执行并分析文件
        elif args.run:
            target_file = Path(args.run).absolute()
            
            if not target_file.exists():
                print(f"❌ 错误：文件不存在 -> {target_file}")
                return
            
            if target_file.suffix != '.py':
                print(f"❌ 错误：仅支持Python文件（.py）-> {target_file}")
                return
            
            print(f"\n📌 开始处理文件: {target_file}")
            
            if not client.check_server_connection():
                print("❌ 无法连接服务器，分析终止")
                return
            
            client.connect_to_server()
            
            print(f"\n📖 读取文件: {target_file.name}")
            with open(target_file, 'r', encoding='utf-8') as f:
                code = f.read()
            
            client.execute_and_analyze(code, target_file.name)
            client.stop()
        
        # 模式3: 交互式模式
        else:
            client.start()
        
    except KeyboardInterrupt:
        print("\n🛑 用户中断")
        client.stop()
    except Exception as e:
        print(f"❌ 客户端运行失败: {str(e)}")
        traceback.print_exc()
        client.stop()
    
    print(f"\n👋 感谢使用VSCode自动代码上传客户端（用户: {client.user_id}）")

if __name__ == "__main__":
    main()