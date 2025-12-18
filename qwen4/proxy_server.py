from flask import Flask, send_from_directory, request, jsonify, Response
import requests
from flask_cors import CORS
import socket
import time
from datetime import datetime, timedelta
import json
import traceback
import subprocess
import threading
import queue
import re
import os
import watchdog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import hashlib

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # 跨域支持

HTML_FOLDER = "."

# -------------------------- 连续对话核心配置 --------------------------
conversation_history = {}  # key=user_id, value=[{"role": ..., "content": ..., "time": ...}]
MAX_HISTORY_ROUNDS = 20    # 最多保留20轮对话（每轮=用户+助手）
MAX_HISTORY_AGE = 3600     # 对话历史1小时后自动过期
# 新增：分点输出提示词（让模型强制分点换行）
POINT_PROMPT = "\n\n请用清晰的分点格式（序号1、2、3...或项目符号）回答，每个要点单独一行，确保易读性。"
# ----------------------------------------------------------------------

# -------------------------- 新增：代码分析配置 --------------------------
def extract_code_between_markers(code_content, start_marker="#***start***#", end_marker="#***end***#"):
    """
    提取两个注释标记之间的代码片段
    :param code_content: 完整的代码文本
    :param start_marker: 起始标记注释
    :param end_marker: 结束标记注释
    :return: 标记之间的代码（无标记则返回空字符串）
    """
    lines = code_content.split('\n')
    in_target_section = False
    target_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        # 检测起始标记
        if stripped_line == start_marker:
            in_target_section = True
            continue  # 跳过起始标记行本身
        # 检测结束标记
        if stripped_line == end_marker:
            in_target_section = False
            break  # 找到结束标记，直接终止遍历
        # 收集区间内的代码
        if in_target_section:
            target_lines.append(line)
    
    return '\n'.join(target_lines).strip()
CODE_ANALYSIS_PROMPTS = {
    "explain": """请分析以下代码，按以下格式回答：
    1. **主要功能**：简要说明代码的主要目的
    2. **工作原理**：解释代码的执行流程
    3. **关键模块**：指出代码中的关键部分
    4. **复杂度分析**：评估时间复杂度和空间复杂度
    5. **潜在问题**：指出可能存在的问题或改进空间
    
    代码：
    {code}""",
    
    "runtime_analysis": """代码运行到关键部分，请进行分析：
    1. **当前状态**：描述代码执行到哪一步
    2. **关键变量**：当前重要变量的值
    3. **性能分析**：当前操作的复杂度
    4. **风险点**：可能出现的错误或异常
    5. **优化建议**：针对当前执行点的优化建议
    
    上下文：
    {context}""",
    
    "comparison": """请比较以下两段代码：
    1. **代码A的优势**：
    2. **代码B的优势**：
    3. **性能差异**：
    4. **可读性对比**：
    5. **推荐方案**：
    
    代码A：
    {code_a}
    
    代码B：
    {code_b}""",
    
    "debug": """请帮我调试以下代码问题：
    1. **错误原因**：分析错误产生的根本原因
    2. **解决方案**：提供具体的修复方案
    3. **修复代码**：给出修复后的完整代码
    4. **预防措施**：如何避免类似问题
    
    代码：
    ```python
    {code}
    ```
    
    错误信息：
    {error}
    
    堆栈跟踪：
    {stack_trace}"""
}

# 代码执行队列和状态跟踪
code_execution_queue = queue.Queue()
execution_results = {}
execution_monitor_thread = None
# --------------------------------------------------------------

# -------------------------- 新增：VSCode集成配置 --------------------------
VSCODE_PROJECT_PATHS = []  # 监控的VSCode项目路径
VSCODE_CODE_SNIPPETS = {}  # 缓存最近运行的代码片段
VSCODE_AUTO_ANALYSIS_CACHE = {}  # 自动分析缓存

class VSCodeFileHandler(FileSystemEventHandler):
    """监控VSCode项目文件变化"""
    def __init__(self, user_id, project_path, auto_upload=False):
        self.user_id = user_id
        self.project_path = project_path
        self.auto_upload = auto_upload
        self.last_modified_times = {}
    
    def on_modified(self, event):
     if event.is_directory:
         return
        
     if event.src_path.endswith('.py'):
        try:
            current_time = time.time()
            file_path = event.src_path
            
            # 防止频繁触发
            if file_path in self.last_modified_times:
                if current_time - self.last_modified_times[file_path] < 2:
                    return
            
            self.last_modified_times[file_path] = current_time
            
            with open(file_path, 'r', encoding='utf-8') as f:
                full_code = f.read()
            # ========== 新增：提取标记区间内的代码 ==========
            target_code = extract_code_between_markers(full_code)
            if not target_code:
                target_code = full_code  # 无标记则用完整代码
            # ==============================================
            
            # 保存最近修改的代码（替换为提取后的代码）
            VSCODE_CODE_SNIPPETS[self.user_id] = {
                'file': file_path,
                'code': target_code,  # 存储提取后的代码
                'time': datetime.now()
            }
            
            print(f"📝 检测到VSCode代码修改: {file_path}")
            
            # 如果启用自动上传，则自动分析
            if self.auto_upload and len(target_code.strip()) > 10:
                analysis_id = f"auto_{int(time.time())}_{hashlib.md5(target_code.encode()).hexdigest()[:8]}"
                
                threading.Thread(
                    target=process_auto_upload_analysis,
                    args=(analysis_id, target_code, self.user_id, os.path.basename(file_path), "save"),
                    daemon=True
                ).start()
                
                print(f"🔄 自动分析已触发: {analysis_id}")
                
        except Exception as e:
            print(f"❌ 读取代码文件失败: {str(e)}")

def start_vscode_monitor(user_id, project_path, auto_upload=False):
    """启动VSCode项目监控"""
    if not os.path.exists(project_path):
        print(f"❌ 项目路径不存在: {project_path}")
        return None
    
    try:
        # 检查是否已经在监控中
        for item in VSCODE_PROJECT_PATHS:
            if item['user_id'] == user_id and item['path'] == project_path:
                print(f"⚠️ 已在监控中: {project_path}")
                return item['observer']
        
        event_handler = VSCodeFileHandler(user_id, project_path, auto_upload)
        observer = Observer()
        observer.schedule(event_handler, project_path, recursive=True)
        observer.start()
        
        VSCODE_PROJECT_PATHS.append({
            'user_id': user_id,
            'path': project_path,
            'observer': observer,
            'auto_upload': auto_upload,
            'start_time': datetime.now()
        })
        print(f"✅ 开始监控VSCode项目: {project_path} (自动上传: {auto_upload})")
        return observer
    except Exception as e:
        print(f"❌ 启动监控失败: {str(e)}")
        traceback.print_exc()
        return None

def stop_vscode_monitor(user_id, project_path=None):
    """停止VSCode项目监控"""
    items_to_remove = []
    for item in VSCODE_PROJECT_PATHS:
        if item['user_id'] == user_id:
            if project_path is None or item['path'] == project_path:
                items_to_remove.append(item)
    
    for item in items_to_remove:
        try:
            item['observer'].stop()
            item['observer'].join()
            VSCODE_PROJECT_PATHS.remove(item)
            print(f"✅ 停止监控VSCode项目: {item['path']}")
        except Exception as e:
            print(f"❌ 停止监控失败: {str(e)}")
            return False
    
    if not items_to_remove:
        print(f"⚠️ 未找到用户 {user_id} 的监控项目")
        return False
    
    return True
# --------------------------------------------------------------

def get_local_ip():
    """自动获取局域网IP，异常时返回127.0.0.1"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        print(f"获取本地IP失败: {str(e)}")
        return "127.0.0.1"

LOCAL_IP = get_local_ip()
OLLAMA_API_URL = f"http://{LOCAL_IP}:11434/api/chat"

# -------------------------- 新增：代码分析函数 --------------------------
def analyze_code(code, analysis_type="explain", context=None):
    """调用大模型分析代码（优先分析标记区间内的代码）"""
    if analysis_type not in CODE_ANALYSIS_PROMPTS:
        analysis_type = "explain"
    
    # 容错处理：确保上下文不为空
    context = context or {}

    # ========== 新增核心逻辑：提取标记区间内的代码 ==========
    target_code = extract_code_between_markers(code)
    if not target_code:
        # 没有找到标记区间，使用完整代码（兼容原有逻辑）
        target_code = code
    # =======================================================
    
    try:
        if analysis_type == "debug":
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(
                code=target_code,  # 替换为提取后的代码
                error=context.get('error', ''),
                stack_trace=context.get('stack_trace', '')
            )
        elif analysis_type == "comparison":
            # 比较模式下，两段代码都要提取标记区间
            code_a = extract_code_between_markers(context.get('code_a', code)) or context.get('code_a', code)
            code_b = extract_code_between_markers(context.get('code_b', '')) or context.get('code_b', '')
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(
                code_a=code_a,
                code_b=code_b
            )
        elif analysis_type == "runtime_analysis":
            context_str = json.dumps(context, ensure_ascii=False, indent=2) if isinstance(context, dict) else str(context)
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(context=context_str)
        else:
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=target_code)  # 替换为提取后的代码
        
        response = requests.post(
            OLLAMA_API_URL,
            json={
                "model": "qwen:7b-chat-q4_0",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("message", {}).get("content", "分析失败")
        else:
            return f"模型调用失败: {response.status_code}"
    except Exception as e:
        return f"分析代码时出错: {str(e)}"
def extract_code_blocks(text):
    """从文本中提取代码块"""
    # 匹配Markdown代码块
    code_pattern = r'```(?:\w+)?\s*([\s\S]*?)```'
    matches = re.findall(code_pattern, text, re.MULTILINE)
    
    if matches:
        return matches
    else:
        # 如果没有代码块，返回整个文本
        return [text]

def execute_code_with_monitoring(code, timeout=30, user_id="anonymous"):
    """执行代码并监控关键点"""
    process = None  # 初始化process变量
    def run_code():
        nonlocal process  # 声明使用外部变量
        try:
            # 创建临时文件
            temp_filename = f'temp_code_{hashlib.md5(code.encode()).hexdigest()[:8]}.py'
            with open(temp_filename, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 启动子进程
            process = subprocess.Popen(
                ['python', temp_filename],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout_lines = []
            stderr_lines = []
            all_output = []
            
            # 读取输出
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    output = output.rstrip('\n')
                    stdout_lines.append(output)
                    all_output.append(output)
                    
                    # 检测关键输出点（可以根据需要自定义规则）
                    if any(keyword in output.lower() for keyword in ['result:', 'output:', 'finished', 'done', 'error:', 'exception:']):
                        # 在关键点触发分析
                        context = {
                            "output": output,
                            "code_snippet": code,
                            "execution_point": "关键输出阶段",
                            "all_output": "\n".join(all_output),
                            "user_id": user_id
                        }
                        
                        # 异步进行分析
                        threading.Thread(
                            target=analyze_runtime_point,
                            args=(context,),
                            daemon=True
                        ).start()
            
            # 收集错误输出
            stderr_output = process.stderr.read()
            if stderr_output:
                stderr_lines.append(stderr_output.strip())
            
            return {
                "success": process.returncode == 0,
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines),
                "returncode": process.returncode,
                "output": "\n".join(all_output)
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "output": ""
            }
        finally:
            # 清理临时文件
            try:
                os.remove(temp_filename)
            except:
                pass
    
    # 在新线程中执行代码
    result_queue = queue.Queue()
    thread = threading.Thread(target=lambda q, c: q.put(run_code()), args=(result_queue, code))
    thread.start()
    thread.join(timeout)
    
    if thread.is_alive():
        # 超时处理
        try:
            process.terminate()
        except:
            pass
        return {
            "success": False,
            "timeout": True,
            "error": f"代码执行超时（{timeout}秒）"
        }
    
    return result_queue.get()

def analyze_runtime_point(context):
    """分析运行时的关键点"""
    try:
        # 关键修复：传递 context 参数给 analyze_code
        analysis = analyze_code(
            "",  # runtime_analysis 不需要 code，传空字符串
            "runtime_analysis",
            context=context  # 直接传递上下文字典
        )
        
        # 保存分析结果
        analysis_id = f"runtime_{int(time.time())}_{hashlib.md5(analysis.encode()).hexdigest()[:8]}"
        VSCODE_AUTO_ANALYSIS_CACHE[analysis_id] = {
            "analysis": analysis,
            "context": context,
            "timestamp": datetime.now().isoformat(),
            "type": "runtime_analysis",
            "status": "completed"
        }
        
        print(f"✅ 运行时分析完成: {analysis_id}")
        
    except Exception as e:
        print(f"❌ 运行时分析失败: {str(e)}")

def process_auto_upload_analysis(analysis_id, code, user_id, filename, trigger_type):
    """处理自动上传的分析"""
    try:
        VSCODE_AUTO_ANALYSIS_CACHE[analysis_id] = {
            "code": code,
            "user_id": user_id,
            "filename": filename,
            "trigger_type": trigger_type,
            "timestamp": datetime.now().isoformat(),
            "status": "analyzing"
        }
        
        # 根据触发类型选择分析方式
        analysis_type = "explain"  # 默认类型
        analysis_context = None    # 初始化上下文
        
        if trigger_type == "run":
            analysis_type = "runtime_analysis"
            # 为 runtime_analysis 构建默认上下文
            analysis_context = {
                "code": code,
                "user_id": user_id,
                "filename": filename,
                "trigger_type": "run",
                "timestamp": datetime.now().isoformat(),
                "status": "running"
            }
        elif trigger_type == "test":
            analysis_type = "comparison"
        else:  # manual, save, debug
            analysis_type = "explain"
        
        # 调用大模型分析（根据类型传参）
        if analysis_type == "runtime_analysis" and analysis_context:
            # runtime_analysis 传 context
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(context=analysis_context)
        elif analysis_type == "comparison":
            # comparison 需要 code_a/code_b，这里默认传相同代码
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code_a=code, code_b=code)
        elif analysis_type == "debug":
            # debug 需要额外参数，这里暂不处理
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=code, error="", stack_trace="")
        else:
            # 其他类型传 code
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=code)
        
        response = requests.post(
            OLLAMA_API_URL,
            json={
                "model": "qwen:7b-chat-q4_0",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            analysis_result = result.get("message", {}).get("content", "分析失败")
            
            # 保存结果
            VSCODE_AUTO_ANALYSIS_CACHE[analysis_id].update({
                "status": "completed",
                "analysis": analysis_result,
                "completion_time": datetime.now().isoformat(),
                "analysis_type": analysis_type
            })
            
            print(f"✅ 自动分析完成: {filename} (ID: {analysis_id})")
            
        else:
            VSCODE_AUTO_ANALYSIS_CACHE[analysis_id].update({
                "status": "failed",
                "error": f"模型调用失败: {response.status_code}"
            })
            
    except Exception as e:
        VSCODE_AUTO_ANALYSIS_CACHE[analysis_id].update({
            "status": "failed",
            "error": str(e)
        })
        print(f"❌ 自动分析处理失败: {str(e)}")
def monitor_code_execution():
    """监控代码执行的线程函数"""
    while True:
        try:
            task = code_execution_queue.get(timeout=1)
            if task is None:  # 停止信号
                break
            
            execution_id, code, user_id = task
            result = execute_code_with_monitoring(code, user_id=user_id)
            
            # 保存结果
            execution_results[execution_id] = {
                "result": result,
                "timestamp": datetime.now().isoformat(),
                "user_id": user_id
            }
            
            # 清理旧结果（保留最近10个）
            if len(execution_results) > 10:
                oldest_key = min(execution_results.keys(), 
                               key=lambda k: execution_results[k]["timestamp"])
                del execution_results[oldest_key]
                
        except queue.Empty:
            continue
        except Exception as e:
            print(f"代码执行监控错误: {str(e)}")

# --------------------------------------------------------------

# 启动监控线程
if execution_monitor_thread is None:
    execution_monitor_thread = threading.Thread(target=monitor_code_execution, daemon=True)
    execution_monitor_thread.start()

def clean_expired_history():
    """清理过期或过长的对话历史"""
    now = datetime.now()
    for user_id in list(conversation_history.keys()):
        history = conversation_history[user_id]
        # 过滤过期消息
        valid_history = [msg for msg in history if (now - msg["time"]).total_seconds() < MAX_HISTORY_AGE]
        # 限制历史长度
        if len(valid_history) > MAX_HISTORY_ROUNDS * 2:
            valid_history = valid_history[-MAX_HISTORY_ROUNDS * 2:]
        # 更新或删除历史
        if valid_history:
            conversation_history[user_id] = valid_history
        else:
            del conversation_history[user_id]

def clean_old_analyses():
    """清理旧的自动分析记录"""
    now = datetime.now()
    to_delete = []
    for analysis_id, record in VSCODE_AUTO_ANALYSIS_CACHE.items():
        if 'timestamp' in record:
            record_time = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
            if (now - record_time).total_seconds() > 86400:  # 24小时
                to_delete.append(analysis_id)
    
    for analysis_id in to_delete:
        del VSCODE_AUTO_ANALYSIS_CACHE[analysis_id]
    
    if to_delete:
        print(f"🧹 清理了 {len(to_delete)} 条旧的自动分析记录")

# 定期清理任务
def schedule_cleanup():
    """定期清理任务"""
    while True:
        time.sleep(3600)  # 每小时清理一次
        clean_expired_history()
        clean_old_analyses()

# 启动定期清理线程
cleanup_thread = threading.Thread(target=schedule_cleanup, daemon=True)
cleanup_thread.start()

# 打印服务启动信息
print("=== 服务启动成功 ===")
print(f"局域网IP：{LOCAL_IP}")
print(f"访问地址：http://{LOCAL_IP}:5000")
print(f"Ollama 转发地址：{OLLAMA_API_URL}")
print(f"连续对话配置：最多{MAX_HISTORY_ROUNDS}轮，{MAX_HISTORY_AGE}秒过期")
print("特性：自动让模型分点换行输出")
print("新增功能：代码自动解析和运行时分析")
print("新增功能：VSCode集成 - 实时代码监控和自动上传")
print("====================")

@app.route('/')
def serve_index():
    return send_from_directory(HTML_FOLDER, 'model-deployment.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口，用于监控服务状态"""
    return jsonify({
        "status": "ok", 
        "timestamp": datetime.now().isoformat(),
        "code_monitor_active": execution_monitor_thread.is_alive() if execution_monitor_thread else False,
        "vscode_monitors": len(VSCODE_PROJECT_PATHS),
        "auto_analyses": len(VSCODE_AUTO_ANALYSIS_CACHE),
        "local_ip": LOCAL_IP
    }), 200

# -------------------------- 新增：代码分析API端点 --------------------------

@app.route('/api/code/analyze', methods=['POST'])
def analyze_code_api():
    """分析代码用途和结构"""
    try:
        data = request.get_json()
        code = data.get("code")
        analysis_type = data.get("type", "explain")
        
        if not code:
            return jsonify({"error": "未提供代码"}), 400
        
        # 提取代码块
        code_blocks = extract_code_blocks(code)
        if not code_blocks:
            return jsonify({"error": "未找到有效代码"}), 400
        
        # 分析第一个代码块
        analysis_result = analyze_code(code_blocks[0], analysis_type)
        
        return jsonify({
            "analysis": analysis_result,
            "code_extracted": code_blocks[0][:500] + ("..." if len(code_blocks[0]) > 500 else ""),
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        error_msg = f"代码分析失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/code/execute', methods=['POST'])
def execute_code_api():
    """执行代码并在关键点进行分析"""
    try:
        data = request.get_json()
        code = data.get("code")
        user_id = data.get("user_id", "anonymous")
        
        if not code:
            return jsonify({"error": "未提供代码"}), 400
        
        # 生成执行ID
        execution_id = f"exec_{int(time.time())}_{hashlib.md5(code.encode()).hexdigest()[:8]}"
        
        # 添加到执行队列
        code_execution_queue.put((execution_id, code, user_id))
        
        # 先进行静态分析
        static_analysis = analyze_code(code, "explain")
        
        return jsonify({
            "execution_id": execution_id,
            "static_analysis": static_analysis,
            "message": "代码已提交执行，将在关键点进行AI分析",
            "timestamp": datetime.now().isoformat()
        }), 202
        
    except Exception as e:
        error_msg = f"代码执行失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/code/result/<execution_id>', methods=['GET'])
def get_execution_result(execution_id):
    """获取代码执行结果"""
    if execution_id not in execution_results:
        return jsonify({"error": "执行结果不存在或已过期"}), 404
    
    result = execution_results[execution_id]
    return jsonify(result), 200

@app.route('/api/code/compare', methods=['POST'])
def compare_code_api():
    """比较两段代码"""
    try:
        data = request.get_json()
        code_a = data.get("code_a")
        code_b = data.get("code_b")
        
        if not code_a or not code_b:
            return jsonify({"error": "需要提供两段代码"}), 400
        
        # 合并为比较提示
        comparison_prompt = CODE_ANALYSIS_PROMPTS["comparison"].format(
            code_a=code_a, 
            code_b=code_b
        )
        
        try:
            response = requests.post(
                OLLAMA_API_URL,
                json={
                    "model": "qwen:7b-chat-q4_0",
                    "messages": [{"role": "user", "content": comparison_prompt}],
                    "stream": False
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                comparison_result = result.get("message", {}).get("content", "比较失败")
            else:
                comparison_result = f"模型调用失败: {response.status_code}"
        except Exception as e:
            comparison_result = f"比较分析时出错: {str(e)}"
        
        return jsonify({
            "comparison": comparison_result,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        error_msg = f"代码比较失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

# -------------------------- 新增：VSCode自动上传相关API --------------------------

@app.route('/api/vscode/auto_analyze', methods=['POST'])
def vscode_auto_analyze():
    """VSCode自动代码分析接口"""
    try:
        data = request.get_json()
        code = data.get("code")
        user_id = data.get("user_id")
        filename = data.get("filename", "unnamed.py")
        trigger_type = data.get("trigger", "manual")  # manual, save, run, test
        
        if not code or not user_id:
            return jsonify({"error": "缺少必要参数"}), 400
        
        print(f"📤 收到VSCode自动上传: {filename} (触发方式: {trigger_type})")
        
        # 生成分析ID
        analysis_id = f"auto_{int(time.time())}_{hashlib.md5(code.encode()).hexdigest()[:8]}"
        
        # 异步进行处理
        threading.Thread(
            target=process_auto_upload_analysis,
            args=(analysis_id, code, user_id, filename, trigger_type),
            daemon=True
        ).start()
        
        return jsonify({
            "analysis_id": analysis_id,
            "message": f"代码已接收，正在AI分析中... (触发方式: {trigger_type})",
            "status_url": f"/api/vscode/auto_status/{analysis_id}",
            "timestamp": datetime.now().isoformat()
        }), 202
        
    except Exception as e:
        error_msg = f"自动分析失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/auto_status/<analysis_id>', methods=['GET'])
def get_auto_analysis_status(analysis_id):
    """获取自动分析状态"""
    if analysis_id not in VSCODE_AUTO_ANALYSIS_CACHE:
        return jsonify({"error": "分析ID不存在"}), 404
    
    result = VSCODE_AUTO_ANALYSIS_CACHE[analysis_id]
    return jsonify(result), 200

@app.route('/api/vscode/recent_analyses', methods=['GET'])
def get_recent_analyses():
    """获取最近的分析记录"""
    try:
        user_id = request.args.get("user_id")
        limit = int(request.args.get("limit", 10))
        
        # 过滤用户的记录
        user_records = []
        for analysis_id, record in VSCODE_AUTO_ANALYSIS_CACHE.items():
            if record.get("user_id") == user_id and record.get("status") == "completed":
                user_records.append({
                    "analysis_id": analysis_id,
                    "filename": record.get("filename"),
                    "timestamp": record.get("timestamp"),
                    "trigger_type": record.get("trigger_type"),
                    "analysis_preview": record.get("analysis", "")[:200] + "..." if len(record.get("analysis", "")) > 200 else record.get("analysis", ""),
                    "analysis_type": record.get("analysis_type", "explain")
                })
        
        # 按时间排序
        user_records.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return jsonify({
            "analyses": user_records[:limit],
            "count": len(user_records[:limit]),
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        error_msg = f"获取分析记录失败: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/analysis_detail/<analysis_id>', methods=['GET'])
def get_analysis_detail(analysis_id):
    """获取分析详情"""
    if analysis_id not in VSCODE_AUTO_ANALYSIS_CACHE:
        return jsonify({"error": "分析ID不存在"}), 404
    
    result = VSCODE_AUTO_ANALYSIS_CACHE[analysis_id]
    
    # 只返回已完成的详情
    if result.get("status") != "completed":
        return jsonify({"error": "分析未完成"}), 400
    
    return jsonify(result), 200

@app.route('/api/vscode/runtime_analyses', methods=['GET'])
def get_runtime_analyses():
    """获取运行时分析记录"""
    try:
        user_id = request.args.get("user_id")
        limit = int(request.args.get("limit", 5))
        
        # 过滤运行时分析
        runtime_records = []
        for analysis_id, record in VSCODE_AUTO_ANALYSIS_CACHE.items():
            if record.get("type") == "runtime_analysis":
                if not user_id or record.get("context", {}).get("user_id") == user_id:
                    runtime_records.append({
                        "analysis_id": analysis_id,
                        "timestamp": record.get("timestamp"),
                        "execution_point": record.get("context", {}).get("execution_point", "未知"),
                        "analysis_preview": record.get("analysis", "")[:200] + "..." if len(record.get("analysis", "")) > 200 else record.get("analysis", "")
                    })
        
        # 按时间排序
        runtime_records.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return jsonify({
            "runtime_analyses": runtime_records[:limit],
            "count": len(runtime_records[:limit]),
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        error_msg = f"获取运行时分析失败: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

# -------------------------- 现有的VSCode集成API端点 --------------------------

@app.route('/api/vscode/connect', methods=['POST'])
def vscode_connect():
    """VSCode连接接口"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        project_path = data.get("project_path")
        auto_upload = data.get("auto_upload", False)
        
        if not user_id or not project_path:
            return jsonify({"error": "缺少必要参数"}), 400
        
        # 检查路径是否存在
        if not os.path.exists(project_path):
            return jsonify({"error": f"项目路径不存在: {project_path}"}), 400
        
        # 启动监控
        observer = start_vscode_monitor(user_id, project_path, auto_upload)
        
        if observer:
            return jsonify({
                "status": "connected",
                "message": f"VSCode项目监控已启动: {project_path}",
                "auto_upload": auto_upload,
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            return jsonify({"error": "启动监控失败"}), 500
        
    except Exception as e:
        error_msg = f"VSCode连接失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/disconnect', methods=['POST'])
def vscode_disconnect():
    """VSCode断开连接接口"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        project_path = data.get("project_path")
        
        if not user_id:
            return jsonify({"error": "缺少user_id"}), 400
        
        # 停止监控
        success = stop_vscode_monitor(user_id, project_path)
        
        if success:
            return jsonify({
                "status": "disconnected",
                "message": "VSCode项目监控已停止",
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            return jsonify({"error": "停止监控失败"}), 500
        
    except Exception as e:
        error_msg = f"VSCode断开连接失败: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/runtest', methods=['POST'])
def vscode_run_test():
    """VSCode运行代码测试接口"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        code = data.get("code")
        test_input = data.get("input", "")
        expected_output = data.get("expected_output", "")
        
        if not user_id or not code:
            return jsonify({"error": "缺少代码内容"}), 400
        
        # 生成执行ID
        execution_id = f"vscode_test_{int(time.time())}_{hashlib.md5(code.encode()).hexdigest()[:8]}"
        
        # 添加到执行队列
        code_execution_queue.put((execution_id, code, user_id))
        
        # 自动分析代码
        analysis = analyze_code(code, "explain")
        
        return jsonify({
            "execution_id": execution_id,
            "analysis": analysis,
            "message": "代码已提交测试，将在运行时进行分析",
            "timestamp": datetime.now().isoformat()
        }), 202
        
    except Exception as e:
        error_msg = f"VSCode测试运行失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/analyze_latest', methods=['POST'])
def vscode_analyze_latest():
    """分析VSCode中最近修改的代码"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        
        if not user_id:
            return jsonify({"error": "缺少user_id"}), 400
        
        # 获取最近修改的代码
        latest_code = VSCODE_CODE_SNIPPETS.get(user_id)
        
        if not latest_code:
            return jsonify({"error": "未找到最近修改的代码"}), 404
        
        # 分析代码
        analysis = analyze_code(latest_code['code'], "explain")
        
        return jsonify({
            "analysis": analysis,
            "file": latest_code['file'],
            "timestamp": latest_code['time'].isoformat(),
            "code_preview": latest_code['code'][:500] + ("..." if len(latest_code['code']) > 500 else "")
        }), 200
        
    except Exception as e:
        error_msg = f"分析最近代码失败: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/debug', methods=['POST'])
def vscode_debug():
    """VSCode调试模式分析"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        code = data.get("code")
        error_message = data.get("error", "")
        stack_trace = data.get("stack_trace", "")
        
        if not user_id or not code:
            return jsonify({"error": "缺少必要参数"}), 400
        
        # 使用新的debug分析函数
        analysis_result = analyze_code(code, "debug", {
            "error": error_message,
            "stack_trace": stack_trace
        })
        
        return jsonify({
            "debug_analysis": analysis_result,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        error_msg = f"调试分析失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

@app.route('/api/vscode/status', methods=['GET'])
def vscode_status():
    """获取VSCode监控状态"""
    try:
        user_id = request.args.get("user_id")
        
        if user_id:
            # 获取指定用户的监控状态
            user_monitors = []
            for item in VSCODE_PROJECT_PATHS:
                if item['user_id'] == user_id:
                    user_monitors.append(item)
            
            latest_code = VSCODE_CODE_SNIPPETS.get(user_id)
            
            # 获取用户的自动分析数量
            user_analyses = len([r for r in VSCODE_AUTO_ANALYSIS_CACHE.values() 
                                if r.get("user_id") == user_id])
            
            return jsonify({
                "monitoring": len(user_monitors) > 0,
                "monitors": [{
                    "project_path": m['path'],
                    "auto_upload": m.get('auto_upload', False),
                    "start_time": m.get('start_time').isoformat() if m.get('start_time') else None
                } for m in user_monitors],
                "latest_code": latest_code is not None,
                "code_file": latest_code['file'] if latest_code else None,
                "code_timestamp": latest_code['time'].isoformat() if latest_code else None,
                "auto_analyses_count": user_analyses,
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            # 获取所有监控状态
            return jsonify({
                "active_monitors": len(VSCODE_PROJECT_PATHS),
                "auto_analyses_total": len(VSCODE_AUTO_ANALYSIS_CACHE),
                "monitors": [
                    {
                        "user_id": item['user_id'],
                        "project_path": item['path'],
                        "auto_upload": item.get('auto_upload', False),
                        "observer_alive": item['observer'].is_alive()
                    }
                    for item in VSCODE_PROJECT_PATHS
                ],
                "timestamp": datetime.now().isoformat()
            }), 200
            
    except Exception as e:
        error_msg = f"获取监控状态失败: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

# --------------------------------------------------------------

@app.route('/api/chat', methods=['POST'])
def proxy_chat():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        new_message = data.get("messages")[-1] if data.get("messages") else None

        # 参数校验
        if not user_id or not new_message or new_message.get("role") != "user":
            return jsonify({"error": "缺少 user_id 或用户消息"}), 400

        # 关键修改1：给用户的问题追加"分点输出"提示词
        enhanced_content = new_message["content"] + POINT_PROMPT
        # 构建增强后的用户消息（不修改原消息，仅传给模型）
        enhanced_new_message = {**new_message, "content": enhanced_content}

        clean_expired_history()
        user_history = conversation_history.get(user_id, [])

        # 关键修改2：用增强后的消息构造上下文（历史消息不变，仅最新消息加提示）
        full_messages = [{"role": msg["role"], "content": msg["content"]} for msg in user_history] + [enhanced_new_message]
        ollama_data = {**data, "messages": full_messages}

        # 调用Ollama API（设置超时为30秒）
        response = requests.post(
            OLLAMA_API_URL,
            json=ollama_data,
            stream=True,
            headers={'Content-Type': 'application/json'},
            timeout=30
        )

        def generate():
            assistant_reply = ""
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk
                    # 解析流式响应中的助手回复
                    try:
                        chunk_str = chunk.decode('utf-8', errors='ignore')
                        for line in chunk_str.split('\n'):
                            line = line.strip()
                            if line and line.startswith('{') and line.endswith('}'):
                                chunk_json = json.loads(line)
                                if chunk_json.get("message") and not chunk_json.get("done"):
                                    assistant_reply += chunk_json["message"]["content"]
                    except Exception as e:
                        print(f"解析流式响应失败: {str(e)}")

            # 保存对话历史（关键：保存用户原始问题，而非带提示的问题）
            if assistant_reply:
                # 保存原始用户消息（不含提示词）
                user_history.append({
                    "role": new_message["role"],
                    "content": new_message["content"],
                    "time": datetime.now()
                })
                # 保存模型分点回答
                user_history.append({
                    "role": "assistant",
                    "content": assistant_reply,
                    "time": datetime.now()
                })
                conversation_history[user_id] = user_history

        return Response(
            generate(),
            status=response.status_code,
            mimetype=response.headers.get('Content-Type', 'application/json')
        )

    except requests.exceptions.Timeout:
        error_msg = "请求Ollama超时，请检查服务响应速度"
        print(error_msg)
        return jsonify({"error": error_msg}), 504
    except requests.exceptions.ConnectionError:
        error_msg = "无法连接到Ollama服务，请检查Ollama是否启动"
        print(error_msg)
        return jsonify({"error": error_msg}), 503
    except Exception as e:
        error_msg = f"服务异常: {str(e)}"
        print(error_msg)
        traceback.print_exc()  # 打印详细异常栈
        return jsonify({"error": error_msg}), 500

# 服务静态HTML文件
@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(HTML_FOLDER, filename)

@app.route('/code_analysis.html')
def serve_code_analysis():
    return send_from_directory(HTML_FOLDER, 'code_analysis.html')

@app.route('/auto_analysis_dashboard.html')
def serve_auto_analysis_dashboard():
    return send_from_directory(HTML_FOLDER, 'auto_analysis_dashboard.html')

# 新增：实时分析状态推送接口（简化版）
@app.route('/api/vscode/stream_updates')
def stream_vscode_updates():
    """SSE流式推送VSCode更新"""
    def generate():
        last_count = 0
        while True:
            time.sleep(2)
            current_count = len(VSCODE_AUTO_ANALYSIS_CACHE)
            
            if current_count != last_count:
                last_count = current_count
                yield f"data: {json.dumps({'analyses_count': current_count, 'timestamp': datetime.now().isoformat()})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    # 创建必要的目录
    if not os.path.exists('temp'):
        os.makedirs('temp')
    
    print("🚀 启动Flask服务...")
    print("📁 服务根目录:", os.path.abspath(HTML_FOLDER))
    print("💡 可用页面:")
    print(f"  1. 聊天界面: http://{LOCAL_IP}:5000/model-deployment.html")
    print(f"  2. 代码分析: http://{LOCAL_IP}:5000/code_analysis.html")
    print(f"  3. 自动分析仪表板: http://{LOCAL_IP}:5000/auto_analysis_dashboard.html")
    print("🔧 VSCode自动上传客户端:")
    print("   python vscode_auto_upload.py --server http://localhost:5000 --project /path/to/project --user your_id")
    print("\n🌟 新功能：")
    print("  • VSCode代码自动上传和分析")
    print("  • 运行时关键点检测")
    print("  • 自动分析历史记录")
    print("  • 实时分析仪表板")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)