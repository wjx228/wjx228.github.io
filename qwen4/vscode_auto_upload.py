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
    def __init__(self, server_url="http://localhost:5000", project_path=None, user_id=None):
        """
        VSCode自动代码上传客户端
        
        Args:
            server_url: 云平台服务器地址
            project_path: VSCode项目路径
            user_id: 用户ID
        """
        self.server_url = server_url
        self.user_id = user_id or f"user_{socket.gethostname()}_{os.getpid()}_{int(time.time())}"
        
        # 固定项目路径为你的demo目录
        self.project_path = Path(r"D:\wjx228.github.io\qwen4\demo").absolute()
        
        self.running = False
        self.connected = False
        self.auto_upload = True  # 默认启用自动上传
        
        # 创建日志目录
        self.log_dir = Path.home() / ".vscode_auto_upload"
        self.log_dir.mkdir(exist_ok=True)
        
        # 设置信号处理
        try:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        except Exception as e:
            print(f"⚠️ 信号处理初始化失败: {str(e)}")
        
        print(f"🚀 VSCode自动代码上传客户端 v1.0")
        print(f"📊 用户ID: {self.user_id}")
        
    def _detect_vscode_project(self):
        """自动检测VSCode项目路径"""
        try:
            current_dir = Path.cwd()
            if (current_dir / '.vscode').exists():
                return current_dir
            
            for parent in current_dir.parents:
                if (parent / '.vscode').exists():
                    return parent
            
            if 'VSCODE_PROJECTS' in os.environ:
                return Path(os.environ['VSCODE_PROJECTS'])
            
            python_files = list(current_dir.glob("*.py"))
            if python_files:
                return current_dir
            
            print(f"⚠️  未检测到VSCode项目，使用当前目录: {current_dir}")
            return current_dir
            
        except Exception as e:
            print(f"⚠️ 检测VSCode项目失败: {str(e)}")
            return Path.cwd()
    
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
        """连接到服务器"""
        try:
            print(f"🔗 正在连接到云平台: {self.server_url}")
            print(f"📁 项目路径: {self.project_path}")
            
            payload = {
                "user_id": self.user_id,
                "project_path": str(self.project_path),
                "auto_upload": self.auto_upload
            }
            
            response = requests.post(
                f"{self.server_url}/api/vscode/connect",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ {result.get('message', '连接成功')}")
                print(f"   自动上传: {'已启用' if self.auto_upload else '已禁用'}")
                self.connected = True
                
                self.open_dashboard()
                
                return True
            else:
                error_msg = response.json().get('error', '未知错误')
                print(f"❌ 连接失败: {error_msg}")
                return False
                
        except Exception as e:
            print(f"❌ 连接时出错: {str(e)}")
            return False
    
    def open_dashboard(self):
        """打开仪表板"""
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
        上传代码进行分析
        
        Args:
            code: 代码内容
            filename: 文件名
            trigger_type: 触发类型 (manual, save, run, test)
        """
        try:
            print(f"📤 上传代码分析: {filename} ({trigger_type})")
            
            payload = {
                "code": code,
                "user_id": self.user_id,
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
            
            print(f"   分析ID: {analysis_id}")
            print(f"   状态: {message}")
            
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
                        print(f"✅ 分析完成: {filename}")
                        print(f"   查看详情: {self.server_url}/auto_analysis_dashboard.html?user_id={self.user_id}")
                        break
                    elif status == "failed":
                        error_msg = status_data.get("error", "分析失败")
                        print(f"❌ 分析失败: {error_msg}")
                        break
                    elif status == "analyzing":
                        if attempt % 5 == 0:
                            print(f"   🔄 分析中... ({attempt*2}秒)")
                else:
                    print(f"⚠️ 检查状态失败: {response.status_code}")
                    
            except Exception as e:
                if attempt == max_attempts - 5:
                    print(f"⚠️ 监控进度异常: {str(e)}")
        
        if attempt == max_attempts - 1:
            print(f"⚠️ 分析超时: {filename}")
    
    def execute_and_analyze(self, code, filename):
        """执行代码并进行运行时分析"""
        try:
            print(f"⚡ 执行代码并分析: {filename}")
            
            static_id = self.upload_code_for_analysis(code, filename, "run")
            
            if not static_id:
                return None
            
            payload = {
                "code": code,
                "user_id": self.user_id
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
            
            print(f"   执行ID: {execution_id}")
            print("   等待执行结果...")
            
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
                        print(f"✅ 执行成功: {filename}")
                        if exec_result.get("output"):
                            output_preview = exec_result["output"][:200]
                            print(f"   输出预览: {output_preview}...")
                    else:
                        print(f"❌ 执行失败: {filename}")
                        if exec_result.get("error"):
                            print(f"   错误: {exec_result['error']}")
                    
                    return result
                    
                elif response.status_code == 404:
                    if attempt == max_attempts - 1:
                        print(f"⚠️ 执行结果不存在或已过期: {execution_id}")
                        return None
                    
            except Exception as e:
                if attempt == max_attempts - 5:
                    print(f"⚠️ 检查执行结果异常: {str(e)}")
        
        print(f"⚠️ 等待执行结果超时: {filename}")
        return None
    
    def manual_upload_current_file(self):
        """手动上传当前文件"""
        try:
            python_files = list(self.project_path.glob("*.py"))
            
            if not python_files:
                print("⚠️ 当前目录没有Python文件")
                return
            
            latest_file = max(python_files, key=lambda f: f.stat().st_mtime)
            
            with open(latest_file, 'r', encoding='utf-8') as f:
                code = f.read()
            
            self.upload_code_for_analysis(code, latest_file.name, "manual")
            
        except Exception as e:
            print(f"❌ 手动上传失败: {str(e)}")
            traceback.print_exc()
    
    def show_status(self):
        """显示当前状态"""
        try:
            response = requests.get(
                f"{self.server_url}/api/vscode/status?user_id={self.user_id}",
                timeout=5
            )
            
            if response.status_code == 200:
                status = response.json()
                
                print("\n" + "="*60)
                print("📊 当前状态")
                print("="*60)
                print(f"👤 用户: {self.user_id}")
                print(f"📁 项目: {self.project_path}")
                print(f"🌐 服务器: {self.server_url}")
                print(f"🔗 连接状态: {'已连接' if status.get('monitoring') else '未连接'}")
                
                if status.get('monitoring'):
                    for monitor in status.get('monitors', []):
                        print(f"   • {monitor.get('project_path')}")
                        print(f"     自动上传: {'✓' if monitor.get('auto_upload') else '✗'}")
                
                if status.get('latest_code'):
                    print(f"📝 最近代码: {status.get('code_file')}")
                    print(f"   时间: {status.get('code_timestamp')}")
                
                print(f"📈 分析记录: {status.get('auto_analyses_count', 0)} 条")
                print("="*60)
                
                return status
            else:
                print("❌ 获取状态失败")
                return None
                
        except Exception as e:
            print(f"❌ 获取状态时出错: {str(e)}")
            traceback.print_exc()
            return None
    
    def interactive_mode(self):
        """交互式模式"""
        print("\n" + "="*60)
        print("🎮 交互模式")
        print("="*60)
        print("命令列表:")
        print("  [s] 显示当前状态")
        print("  [u] 手动上传当前文件")
        print("  [r] 重新连接服务器")
        print("  [d] 打开仪表板")
        print("  [h] 显示帮助")
        print("  [q] 退出")
        print("="*60)
        
        while self.running:
            try:
                cmd = input("\n请输入命令: ").strip().lower()
                
                if cmd == 's':
                    self.show_status()
                elif cmd == 'u':
                    self.manual_upload_current_file()
                elif cmd == 'r':
                    self.connect_to_server()
                elif cmd == 'd':
                    self.open_dashboard()
                elif cmd == 'h':
                    self.show_help()
                elif cmd == 'q':
                    print("退出交互模式")
                    break
                else:
                    print("未知命令，请输入 s, u, r, d, h, q")
                    
            except KeyboardInterrupt:
                print("\n退出交互模式")
                break
            except Exception as e:
                print(f"命令执行失败: {str(e)}")
                traceback.print_exc()
    
    def show_help(self):
        """显示帮助信息"""
        print("\n" + "="*60)
        print("📖 帮助信息")
        print("="*60)
        print("自动上传功能:")
        print("  1. 保存.py文件时会自动上传分析")
        print("  2. 分析结果会显示在网页仪表板")
        print("  3. 代码运行时会有运行时分析")
        print()
        print("网页界面:")
        print(f"  • 仪表板: {self.server_url}/auto_analysis_dashboard.html")
        print(f"  • 代码分析: {self.server_url}/code_analysis.html")
        print(f"  • AI聊天: {self.server_url}/model-deployment.html")
        print()
        print("监控目录:")
        print(f"  {self.project_path}")
        print("="*60)
    
    def start(self):
        """启动客户端"""
        print("\n" + "="*60)
        print("🚀 启动VSCode自动代码上传客户端")
        print("="*60)
        
        if not self.check_server_connection():
            print("❌ 无法连接到服务器，请确保服务器已启动")
            print("   启动命令: python proxy_server.py")
            return False
        
        if not self.connect_to_server():
            return False
        
        self.running = True
        
        self.show_status()
        
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
                print("✅ 已断开服务器连接")
            
            print("🛑 客户端已停止")
            
        except Exception as e:
            print(f"⚠️ 停止时出错: {str(e)}")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='VSCode自动代码上传客户端')
    parser.add_argument('--server', default='http://localhost:5000', 
                       help='云平台服务器地址 (默认: http://localhost:5000)')
    parser.add_argument('--project', help='VSCode项目路径 (默认: 自动检测)')
    parser.add_argument('--user', help='用户ID (默认: 自动生成)')
    parser.add_argument('--no-auto', action='store_true', 
                       help='禁用自动上传')
    parser.add_argument('--run', help='指定要运行并分析的Python文件路径')
    
    args = parser.parse_args()
    
    client = VSCodeAutoUploadClient(
        server_url=args.server,
        project_path=args.project,
        user_id=args.user
    )
    
    client.auto_upload = not args.no_auto
    
    try:
        if args.run:
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
        
        else:
            client.start()
        
    except KeyboardInterrupt:
        print("\n🛑 用户中断")
        client.stop()
    except Exception as e:
        print(f"❌ 客户端运行失败: {str(e)}")
        traceback.print_exc()
        client.stop()
    
    print("\n👋 感谢使用VSCode自动代码上传客户端")

if __name__ == "__main__":
    main()