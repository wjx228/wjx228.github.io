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
import ast  # 新增：用于代码安全分析

app = Flask(__name__)
CORS(app, 
     resources={r"/*": {
         "origins": "*",          # 允许所有来源
         "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],  # 允许所有请求方法
         "allow_headers": "*",    # 允许所有请求头
         "expose_headers": "*"    # 暴露所有响应头
     }},
     supports_credentials=True)   # 支持凭证（如Cookie）

HTML_FOLDER = "/home/wjxwjx/wjx228.github.io/qwen4"
# 确保目录存在（防止路径写错导致找不到文件）
os.makedirs(HTML_FOLDER, exist_ok=True)  

# 统一Ollama配置
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_MODEL_NAME = "qwen:7b-chat-q4_0"

# 分点输出提示词（让模型强制分点换行）
POINT_PROMPT = "\n\n请用清晰的分点格式（序号1、2、3...或项目符号）回答，每个要点单独一行，确保易读性。"

# ========== 代码安全性检查函数 ==========
def validate_code_safety(code):
    """检查代码安全性"""
    # 禁止的危险模块
    dangerous_modules = ['os', 'sys', 'subprocess', 'shutil', 'glob', 'importlib', '__builtins__']
    
    # 禁止的危险函数/属性访问
    dangerous_calls = [
        'eval', 'exec', 'compile', 'open', 'input',
        '__import__', 'getattr', 'setattr', 'delattr',
        'exit', 'quit', 'breakpoint'
    ]
    
    # 尝试解析AST
    try:
        tree = ast.parse(code)
        
        for node in ast.walk(tree):
            # 检查导入
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(dm in alias.name for dm in dangerous_modules):
                        return False, f"禁止导入危险模块: {alias.name}"
                    
            # 检查from...import
            elif isinstance(node, ast.ImportFrom):
                if node.module and any(dm in node.module for dm in dangerous_modules):
                    return False, f"禁止从危险模块导入: {node.module}"
                    
            # 检查函数调用
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in dangerous_calls:
                        return False, f"禁止调用危险函数: {node.func.id}"
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in dangerous_calls:
                        return False, f"禁止调用危险方法: {node.func.attr}"
                        
    except SyntaxError as e:
        # 语法错误，但允许执行（Python会自己报错）
        return True, f"语法检查通过（语法错误会在执行时暴露: {str(e)}）"
    except Exception as e:
        return False, f"代码安全检查失败: {str(e)}"
    
    return True, "代码安全检查通过"

# ========== 统一的聊天接口 ==========
@app.route('/api/chat', methods=['POST'])
def chat_endpoint():
    """
    统一的聊天接口，支持流式和非流式响应
    前端可以通过 stream 参数控制是否使用流式响应
    """
    try:
        # 1. 获取请求数据
        request_data = request.get_json()
        if not request_data:
            return jsonify({"error": "请求数据为空"}), 400
        
        # 2. 提取参数
        user_id = request_data.get("user_id", "default_user")
        messages = request_data.get("messages", [])
        stream = request_data.get("stream", True)  # 默认流式
        temperature = request_data.get("temperature", 0.7)
        max_tokens = request_data.get("max_tokens", 2048)
        
        # 3. 给最后一条用户消息追加分点提示词
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += POINT_PROMPT
        
        # 4. 构建Ollama请求
        ollama_request = {
            "model": OLLAMA_MODEL_NAME,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        # 5. 流式响应处理
        if stream:
            try:
                response = requests.post(
                    OLLAMA_CHAT_URL,
                    json=ollama_request,
                    stream=True,
                    timeout=60
                )
                
                def generate():
                    assistant_reply = ""
                    for chunk in response.iter_lines():
                        if chunk:
                            try:
                                chunk_data = json.loads(chunk.decode('utf-8'))
                                if chunk_data.get("message") and not chunk_data.get("done"):
                                    content = chunk_data["message"].get("content", "")
                                    assistant_reply += content
                                    # 返回原始chunk保持兼容性
                                    yield chunk + b'\n'
                            except json.JSONDecodeError:
                                # 如果不是JSON，直接返回
                                yield chunk + b'\n'
                            except Exception:
                                yield chunk + b'\n'
                    
                    # 保存对话历史（异步）
                    threading.Thread(
                        target=save_conversation_history,
                        args=(user_id, messages[-1]["content"].replace(POINT_PROMPT, ""), assistant_reply),
                        daemon=True
                    ).start()
                
                return Response(generate(), mimetype="application/json")
                
            except requests.exceptions.ConnectionError:
                return jsonify({"error": "无法连接到 Ollama 服务，请检查 11434 端口是否运行"}), 503
            except requests.exceptions.Timeout:
                return jsonify({"error": "Ollama 响应超时，请重试"}), 504
                
        # 6. 非流式响应处理
        else:
            try:
                response = requests.post(
                    OLLAMA_CHAT_URL,
                    json=ollama_request,
                    stream=False,
                    timeout=60
                )
                
                if response.status_code == 200:
                    result = response.json()
                    assistant_reply = result.get("message", {}).get("content", "")
                    
                    # 保存对话历史
                    save_conversation_history(
                        user_id, 
                        messages[-1]["content"].replace(POINT_PROMPT, ""), 
                        assistant_reply
                    )
                    
                    return jsonify({
                        "response": assistant_reply,
                        "model": OLLAMA_MODEL_NAME,
                        "done": True
                    }), 200
                else:
                    return jsonify({"error": f"Ollama 服务错误: {response.status_code}"}), response.status_code
                    
            except requests.exceptions.ConnectionError:
                return jsonify({"error": "无法连接到 Ollama 服务"}), 503
            except requests.exceptions.Timeout:
                return jsonify({"error": "Ollama 响应超时"}), 504
    
    except Exception as e:
        print(f"聊天接口错误：{str(e)}")
        traceback.print_exc()
        return jsonify({"error": f"服务器内部错误：{str(e)}"}), 500

# ========== 对话历史管理 ==========
conversation_history = {}  # key=user_id, value=[{"role": ..., "content": ..., "time": ...}]
MAX_HISTORY_ROUNDS = 20    # 最多保留20轮对话
MAX_HISTORY_AGE = 3600     # 1小时后自动过期

def save_conversation_history(user_id, user_message, assistant_reply):
    """保存对话历史"""
    try:
        if user_id not in conversation_history:
            conversation_history[user_id] = []
        
        history = conversation_history[user_id]
        
        # 添加用户消息
        history.append({
            "role": "user",
            "content": user_message,
            "time": datetime.now()
        })
        
        # 添加助手回复
        history.append({
            "role": "assistant",
            "content": assistant_reply,
            "time": datetime.now()
        })
        
        # 限制历史长度
        if len(history) > MAX_HISTORY_ROUNDS * 2:
            conversation_history[user_id] = history[-MAX_HISTORY_ROUNDS * 2:]
            
    except Exception as e:
        print(f"保存对话历史失败: {str(e)}")

def clean_expired_history():
    """清理过期对话历史"""
    now = datetime.now()
    for user_id in list(conversation_history.keys()):
        history = conversation_history[user_id]
        valid_history = [msg for msg in history if (now - msg["time"]).total_seconds() < MAX_HISTORY_AGE]
        
        if valid_history:
            conversation_history[user_id] = valid_history
        else:
            del conversation_history[user_id]

def extract_code_between_markers(code_content, start_marker="#***start***#", end_marker="#***end***#"):
    """增强版的代码提取函数"""
    print(f" 开始提取代码，内容长度: {len(code_content)}")
    print(f" 查找标签: [{start_marker}] 和 [{end_marker}]")
    
    # 调试：显示前几行和后几行
    print("代码内容预览:")
    lines = code_content.split('\n')
    for i, line in enumerate(lines[:10]):
        print(f"  行{i}: '{line}'")
    if len(lines) > 10:
        print(f"  ... (省略{len(lines)-10}行)")
        for i, line in enumerate(lines[-5:], start=len(lines)-5):
            print(f"  行{i}: '{line}'")
    
    # 方法1：使用字符串查找（更灵活）
    start_idx = code_content.find(start_marker)
    end_idx = code_content.find(end_marker)
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # 找到两个标记
        start_idx += len(start_marker)
        extracted = code_content[start_idx:end_idx].strip()
        print(f"方法1：成功提取代码，长度: {len(extracted)}")
        print(f"提取内容前200字符: {extracted[:200]}")
        return extracted
    else:
        print(f"方法1：未找到标记或标记顺序错误")
        print(f"  开始标记位置: {start_idx}")
        print(f"  结束标记位置: {end_idx}")
    
    # 方法2：使用行遍历（备份方案）
    print("🔄 尝试方法2：行遍历提取")
    in_target_section = False
    target_lines = []
    exact_matches_found = 0
    
    for i, line in enumerate(lines):
        # 检查是否包含精确标记（去除前后空格）
        stripped = line.strip()
        
        # 检查开始标记
        if start_marker in stripped:
            print(f" 行{i} 找到开始标记: '{stripped}'")
            exact_matches_found += 1
            in_target_section = True
            continue
        
        # 检查结束标记
        if end_marker in stripped:
            print(f" 行{i} 找到结束标记: '{stripped}'")
            exact_matches_found += 1
            in_target_section = False
            break
        
        # 如果在目标区域内，保存代码
        if in_target_section:
            target_lines.append(line)
    
    result = '\n'.join(target_lines).strip()
    print(f" 方法2提取结果:")
    print(f"  找到的标记数量: {exact_matches_found}")
    print(f"  提取行数: {len(target_lines)}")
    print(f"  结果长度: {len(result)}")
    
    if result:
        print(f" 提取内容预览:")
        lines_preview = result.split('\n')
        for i, line in enumerate(lines_preview[:10]):
            print(f"  行{i}: {line}")
        if len(lines_preview) > 10:
            print(f"  ... (还有{len(lines_preview)-10}行)")
    else:
        print(" 未提取到任何内容")
        
        # 尝试查找可能的标记变体
        print(" 搜索可能的标记变体:")
        for i, line in enumerate(lines):
            if 'start' in line.lower() or 'end' in line.lower():
                print(f"  行{i} 可能包含标记: '{line.strip()}'")
    
    return result

# ========== 智能标签检测函数 ==========
def smart_detect_markers(code_content, start_marker="#***start***#", end_marker="#***end***#"):
    """
    智能检测代码中的标签
    返回: {
        "found_markers": True/False,  # 是否找到完整标签对
        "is_valid_snippet": True/False,  # 是否提取到有效代码片段
        "extracted_code": "",  # 提取的代码
        "marker_count": 0,  # 找到的标签数量
        "message": "",  # 检测结果消息
        "original_length": len(code_content),
        "extracted_length": 0
    }
    """
    print(f" 智能检测开始，代码长度: {len(code_content)}")
    
    # 检查是否包含标记
    has_start_marker = start_marker in code_content
    has_end_marker = end_marker in code_content
    
    # 情况1：完全没有标记
    if not has_start_marker and not has_end_marker:
        print(f"❌ 未检测到任何标记")
        return {
            "found_markers": False,
            "is_valid_snippet": False,
            "extracted_code": "",
            "marker_count": 0,
            "message": "❌ 未检测到标记 #***start***# 和 #***end***#",
            "original_length": len(code_content),
            "extracted_length": 0
        }
    
    # 情况2：只有部分标记
    if has_start_marker ^ has_end_marker:  # 异或，只有一个标记
        missing_marker = end_marker if has_start_marker else start_marker
        print(f"⚠️ 只检测到部分标记，缺少: {missing_marker}")
        return {
            "found_markers": False,
            "is_valid_snippet": False,
            "extracted_code": "",
            "marker_count": 1,
            "message": f"⚠️ 只检测到部分标记，请同时添加 {start_marker} 和 {end_marker}",
            "original_length": len(code_content),
            "extracted_length": 0
        }
    
    # 情况3：有完整标记对，尝试提取
    print(f"✅ 检测到完整标记对")
    
    # 使用字符串查找提取
    start_idx = code_content.find(start_marker)
    end_idx = code_content.find(end_marker)
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        start_idx += len(start_marker)
        extracted_code = code_content[start_idx:end_idx].strip()
        
        if extracted_code:
            print(f"✅ 成功提取代码片段，长度: {len(extracted_code)}")
            return {
                "found_markers": True,
                "is_valid_snippet": True,
                "extracted_code": extracted_code,
                "marker_count": 2,
                "message": "✅ 成功检测并提取代码片段",
                "original_length": len(code_content),
                "extracted_length": len(extracted_code)
            }
        else:
            print(f"⚠️ 标记间没有代码内容")
            return {
                "found_markers": True,
                "is_valid_snippet": False,
                "extracted_code": "",
                "marker_count": 2,
                "message": "⚠️ 检测到标记但标记之间没有代码内容",
                "original_length": len(code_content),
                "extracted_length": 0
            }
    
    # 情况4：标记顺序错误
    print(f"❌ 标记顺序错误")
    return {
        "found_markers": False,
        "is_valid_snippet": False,
        "extracted_code": "",
        "marker_count": 2,
        "message": "❌ 标记顺序错误，请确保 #***start***# 在 #***end***# 之前",
        "original_length": len(code_content),
        "extracted_length": 0
    }

# ========== 代码分析配置 ==========
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

# ========== 代码执行函数（增强安全性） ==========
def execute_code_with_monitoring(code, timeout=30, user_id="anonymous"):
    """执行代码并监控关键点（增强安全性）"""
    process = None
    
    # 1. 安全检查
    is_safe, safety_msg = validate_code_safety(code)
    if not is_safe:
        return {
            "success": False,
            "error": f"代码安全检查失败: {safety_msg}",
            "output": "",
            "safety_check": False
        }
    
    def run_code():
        nonlocal process
        try:
            # 2. 创建临时文件
            temp_dir = "temp_execution"
            os.makedirs(temp_dir, exist_ok=True)
            
            temp_filename = f'{temp_dir}/temp_code_{hashlib.md5(code.encode()).hexdigest()[:8]}.py'
            with open(temp_filename, 'w', encoding='utf-8') as f:
                f.write("# 安全沙箱执行代码\n")
                f.write("# 自动生成的安全封装\n")
                f.write(code)
            
            # 3. 在受限环境中执行
            env = os.environ.copy()
            env['PYTHONPATH'] = ''  # 清空PYTHONPATH
            
            process = subprocess.Popen(
                ['python', temp_filename],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=temp_dir  # 在临时目录执行
            )
            
            stdout_lines = []
            stderr_lines = []
            all_output = []
            
            # 4. 读取输出
            start_time = time.time()
            while True:
                if process.poll() is not None:
                    # 进程已结束，读取剩余输出
                    remaining_stdout = process.stdout.read()
                    if remaining_stdout:
                        stdout_lines.append(remaining_stdout.strip())
                        all_output.append(remaining_stdout.strip())
                    break
                
                # 读取一行输出
                output = process.stdout.readline()
                if output:
                    output = output.rstrip('\n')
                    stdout_lines.append(output)
                    all_output.append(output)
                    
                    # 检测关键输出点
                    if any(keyword in output.lower() for keyword in ['result:', 'output:', 'finished', 'done', 'error:', 'exception:', 'warning:']):
                        context = {
                            "output": output,
                            "code_snippet": code[:500],
                            "execution_point": "关键输出阶段",
                            "all_output": "\n".join(all_output[-10:]),  # 最近10行
                            "user_id": user_id,
                            "timestamp": time.time()
                        }
                        
                        # 异步进行分析
                        threading.Thread(
                            target=analyze_runtime_point,
                            args=(context,),
                            daemon=True
                        ).start()
                
                # 超时检查
                if time.time() - start_time > timeout:
                    break
                time.sleep(0.1)  # 避免CPU占用过高
            
            # 5. 收集错误输出
            stderr_output = process.stderr.read()
            if stderr_output:
                stderr_lines.append(stderr_output.strip())
            
            # 6. 确保进程终止
            if process.poll() is None:
                process.terminate()
                time.sleep(0.5)
                if process.poll() is None:
                    process.kill()
            
            return {
                "success": process.returncode == 0,
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines),
                "returncode": process.returncode,
                "output": "\n".join(all_output),
                "safety_check": True
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "output": "",
                "safety_check": True
            }
        finally:
            # 7. 清理临时文件
            try:
                if 'temp_filename' in locals():
                    os.remove(temp_filename)
            except:
                pass
    
    # 在新线程中执行代码
    result_queue = queue.Queue()
    thread = threading.Thread(target=lambda q: q.put(run_code()), args=(result_queue,))
    thread.start()
    thread.join(timeout + 5)  # 额外5秒缓冲
    
    if thread.is_alive():
        # 超时处理
        try:
            if process:
                process.terminate()
                time.sleep(1)
                if process.poll() is None:
                    process.kill()
        except:
            pass
        
        thread.join(2)  # 等待线程结束
        
        return {
            "success": False,
            "timeout": True,
            "error": f"代码执行超时（{timeout}秒）",
            "safety_check": True
        }
    
    return result_queue.get()

def extract_code_blocks(text):
    """从文本中提取代码块（如果需要支持Markdown格式）"""
    code_pattern = r'```(?:\w+)?\s*([\s\S]*?)```'
    matches = re.findall(code_pattern, text, re.MULTILINE)
    
    if matches:
        return matches
    else:
        # 如果没有Markdown代码块，返回原始文本
        return [text]

def analyze_code(code, analysis_type="explain", context=None):
    """调用大模型分析代码"""
    if analysis_type not in CODE_ANALYSIS_PROMPTS:
        analysis_type = "explain"
    
    context = context or {}
    
    try:
        if analysis_type == "debug":
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(
                code=code,
                error=context.get('error', ''),
                stack_trace=context.get('stack_trace', '')
            )
        elif analysis_type == "comparison":
            code_a = context.get('code_a', code)
            code_b = context.get('code_b', '')
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(
                code_a=code_a,
                code_b=code_b
            )
        elif analysis_type == "runtime_analysis":
            context_str = json.dumps(context, ensure_ascii=False, indent=2) if isinstance(context, dict) else str(context)
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(context=context_str)
        else:
            prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=code)
        
        response = requests.post(
            OLLAMA_CHAT_URL,
            json={
                "model": OLLAMA_MODEL_NAME,
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

def analyze_runtime_point(context):
    """分析运行时的关键点"""
    try:
        analysis = analyze_code(
            "",
            "runtime_analysis",
            context=context
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

def monitor_code_execution():
    """监控代码执行的线程函数"""
    while True:
        try:
            task = code_execution_queue.get(timeout=1)
            if task is None:  # 停止信号
                break
            
            execution_id, code, user_id = task
            result = execute_code_with_monitoring(code, timeout=30, user_id=user_id)  # 修复：添加timeout参数
            
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

# ========== VSCode集成配置 ==========
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
                
                # 防止频繁触发（2秒内不重复）
                if file_path in self.last_modified_times:
                    if current_time - self.last_modified_times[file_path] < 2:
                        return
                
                self.last_modified_times[file_path] = current_time
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    full_code = f.read()
                
                # 提取标记区间内的代码
                target_code = extract_code_between_markers(full_code)
                if not target_code:
                    target_code = full_code
                
                # 保存最近修改的代码
                VSCODE_CODE_SNIPPETS[self.user_id] = {
                    'file': file_path,
                    'code': target_code,
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

def process_auto_upload_analysis(analysis_id, code, user_id, filename, trigger_type):
    """处理自动上传的分析"""
    try:
        # 先检测标签
        detection_result = smart_detect_markers(code)
        
        if detection_result["found_markers"] and detection_result["is_valid_snippet"]:
            # 有标记且有代码 -> 分析
            extracted_code = detection_result["extracted_code"]
            
            VSCODE_AUTO_ANALYSIS_CACHE[analysis_id] = {
                "code": extracted_code,
                "user_id": user_id,
                "filename": filename,
                "trigger_type": trigger_type,
                "timestamp": datetime.now().isoformat(),
                "status": "analyzing",
                "detection_result": detection_result
            }
            
            # 根据触发类型选择分析方式
            analysis_type = "explain"
            if trigger_type == "run":
                analysis_type = "runtime_analysis"
            elif trigger_type == "test":
                analysis_type = "comparison"
            elif trigger_type == "debug":
                analysis_type = "debug"
            else:
                analysis_type = "explain"
            
            # 调用大模型分析
            if analysis_type == "runtime_analysis":
                context = {
                    "code": extracted_code,
                    "user_id": user_id,
                    "filename": filename,
                    "trigger_type": trigger_type,
                    "timestamp": datetime.now().isoformat(),
                    "status": "running"
                }
                prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(context=json.dumps(context, ensure_ascii=False))
            elif analysis_type == "comparison":
                prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code_a=extracted_code, code_b=extracted_code)
            elif analysis_type == "debug":
                prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=extracted_code, error="", stack_trace="")
            else:
                prompt = CODE_ANALYSIS_PROMPTS[analysis_type].format(code=extracted_code)
            
            response = requests.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": OLLAMA_MODEL_NAME,
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
        else:
            # 没有标记或标记不完整 -> 记录但不分析
            VSCODE_AUTO_ANALYSIS_CACHE[analysis_id] = {
                "code": code,
                "user_id": user_id,
                "filename": filename,
                "trigger_type": trigger_type,
                "timestamp": datetime.now().isoformat(),
                "status": "skipped",
                "detection_result": detection_result,
                "analysis": detection_result["message"]
            }
            print(f"⏭️ 自动分析跳过（无标记）: {filename}")
            
    except Exception as e:
        VSCODE_AUTO_ANALYSIS_CACHE[analysis_id].update({
            "status": "failed",
            "error": str(e)
        })
        print(f"❌ 自动分析处理失败: {str(e)}")

# ========== 清理和监控线程 ==========
def clean_old_analyses():
    """清理旧的自动分析记录"""
    now = datetime.now()
    to_delete = []
    for analysis_id, record in VSCODE_AUTO_ANALYSIS_CACHE.items():
        if 'timestamp' in record:
            try:
                record_time = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
                if (now - record_time).total_seconds() > 86400:  # 24小时
                    to_delete.append(analysis_id)
            except:
                pass
    
    for analysis_id in to_delete:
        del VSCODE_AUTO_ANALYSIS_CACHE[analysis_id]
    
    if to_delete:
        print(f"🧹 清理了 {len(to_delete)} 条旧的自动分析记录")

def schedule_cleanup():
    """定期清理任务（优化性能）"""
    while True:
        time.sleep(3600)  # 每小时清理一次
        try:
            clean_expired_history()
            clean_old_analyses()
        except Exception as e:
            print(f"清理任务出错: {str(e)}")

# ========== 启动监控线程 ==========
if execution_monitor_thread is None:
    execution_monitor_thread = threading.Thread(target=monitor_code_execution, daemon=True)
    execution_monitor_thread.start()

# 启动定期清理线程
cleanup_thread = threading.Thread(target=schedule_cleanup, daemon=True)
cleanup_thread.start()

# ========== 获取本地IP ==========
def get_local_ip():
    """自动获取局域网IP"""
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

# ========== 调试端点 ==========
@app.route('/api/debug/extract_test', methods=['POST'])
def debug_extract_test():
    """调试代码提取功能"""
    try:
        data = request.get_json()
        code = data.get("code", "")
        start_marker = data.get("start_marker", "#***start***#")
        end_marker = data.get("end_marker", "#***end***#")
        
        # 智能检测
        detection_result = smart_detect_markers(code, start_marker, end_marker)
        
        return jsonify({
            "detection_result": detection_result,
            "example_markers": {
                "start": "#***start***#",
                "end": "#***end***#"
            },
            "sample_code_with_markers": """# 示例代码
print("普通代码")

#***start***#
# 这是要分析的代码片段
def calculate_sum(n):
    total = 0
    for i in range(n):
        total += i
    return total

result = calculate_sum(10)
print(f"结果: {result}")
#***end***#

print("代码结束")""",
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/example/markers', methods=['GET'])
def show_marker_example():
    """显示正确的标记使用示例"""
    example_code = """
# 这是普通的Python代码
print("Hello World")

#***start***#
# 这是要提取的代码片段
def important_function():
    '''这个函数会被AI分析'''
    result = 0
    for i in range(10):
        result += i
    return result

print(f"计算结果: {important_function()}")
#***end***#

# 这是标记后的代码
print("分析完成")
"""
    
    return jsonify({
        "example": example_code,
        "markers": {
            "start": "#***start***#",
            "end": "#***end***#"
        },
        "instructions": "将上述标记放在需要分析的代码片段前后，确保标记独占一行或在一行的开头"
    }), 200

# ========== API端点 ==========
@app.route('/')
def serve_index():
    return send_from_directory(HTML_FOLDER, 'model-deployment.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        "status": "ok", 
        "timestamp": datetime.now().isoformat(),
        "code_monitor_active": execution_monitor_thread.is_alive() if execution_monitor_thread else False,
        "vscode_monitors": len(VSCODE_PROJECT_PATHS),
        "auto_analyses": len(VSCODE_AUTO_ANALYSIS_CACHE),
        "local_ip": LOCAL_IP,
        "ollama_url": OLLAMA_CHAT_URL,
        "model": OLLAMA_MODEL_NAME
    }), 200

# ========== 核心修改：只在有标签时分析，否则直接拒绝 ==========
@app.route('/api/code/analyze', methods=['POST'])
def analyze_code_api():
    """智能代码分析API：只在检测到标签时进行分析，否则直接拒绝"""
    try:
        data = request.get_json()
        code = data.get("code")
        analysis_type = data.get("type", "explain")
        
        if not code:
            return jsonify({"error": "未提供代码"}), 400
        
        print(f"📋 收到代码分析请求，代码长度: {len(code)}")
        
        # 智能检测标签
        detection_result = smart_detect_markers(code)
        print(f"🔍 检测结果: {detection_result['message']}")
        
        # 情况1：有完整标签且有有效代码片段 -> 分析
        if detection_result["found_markers"] and detection_result["is_valid_snippet"]:
            print(f"✅ 检测到有效代码片段，开始分析")
            extracted_code = detection_result["extracted_code"]
            
            # 分析代码（使用对应类型的提示词）
            analysis_result = analyze_code(extracted_code, analysis_type)
            
            return jsonify({
                "analysis": analysis_result,
                "detection": detection_result,
                "code_preview": extracted_code[:200] + ("..." if len(extracted_code) > 200 else ""),
                "analysis_performed": True,
                "timestamp": datetime.now().isoformat()
            }), 200
            
        # 情况2：有标签但标记之间没有代码内容
        elif detection_result["found_markers"] and not detection_result["is_valid_snippet"]:
            print(f"⚠️ 检测到标签但无代码内容")
            
            return jsonify({
                "analysis": "⚠️ 检测到标签但标记之间没有代码内容，请在 #***start***# 和 #***end***# 之间添加要分析的代码。",
                "detection": detection_result,
                "code_preview": "",
                "analysis_performed": False,
                "timestamp": datetime.now().isoformat()
            }), 200
            
        # 情况3：没有标签或标签不完整 -> 直接拒绝分析
        else:
            print(f"❌ 未检测到完整标签，拒绝分析")
            
            return jsonify({
                "analysis": f"❌ {detection_result['message']}\n\n💡 使用方法：在代码中使用 #***start***# 和 #***end***# 标记包围要分析的代码片段。",
                "detection": detection_result,
                "code_preview": "",
                "analysis_performed": False,
                "timestamp": datetime.now().isoformat()
            }), 200
        
    except Exception as e:
        error_msg = f"代码分析失败: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({"error": error_msg}), 500

# ========== 以下是其他所有功能（保持不变） ==========
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
        
        # 进行静态分析
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
        
        comparison_prompt = CODE_ANALYSIS_PROMPTS["comparison"].format(
            code_a=code_a, 
            code_b=code_b
        )
        
        try:
            response = requests.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": OLLAMA_MODEL_NAME,
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

@app.route('/api/vscode/auto_analyze', methods=['POST'])
def vscode_auto_analyze():
    """VSCode自动代码分析接口"""
    try:
        data = request.get_json()
        code = data.get("code")
        user_id = data.get("user_id")
        filename = data.get("filename", "unnamed.py")
        trigger_type = data.get("trigger", "manual")
        
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
            if record.get("user_id") == user_id and record.get("status") in ["completed", "skipped", "failed"]:
                user_records.append({
                    "analysis_id": analysis_id,
                    "filename": record.get("filename"),
                    "timestamp": record.get("timestamp"),
                    "trigger_type": record.get("trigger_type"),
                    "status": record.get("status"),
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
    
    if result.get("status") not in ["completed", "skipped", "failed"]:
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
        
        # 使用debug分析
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

# ========== 静态文件服务 ==========
@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(HTML_FOLDER, filename)

@app.route('/code_analysis.html')
def serve_code_analysis():
    return send_from_directory(HTML_FOLDER, 'code_analysis.html')

@app.route('/auto_analysis_dashboard.html')
def serve_auto_analysis_dashboard():
    return send_from_directory(HTML_FOLDER, 'auto_analysis_dashboard.html')

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

# ========== 主程序入口 ==========
if __name__ == '__main__':
    # 设置时区为中国标准时间
    try:
        import os
        os.environ['TZ'] = 'Asia/Shanghai'
        import time
        time.tzset()
    except (ImportError, AttributeError):
        print("⚠️  无法设置时区，日志时间可能为UTC时间")
        pass
    
    # 创建必要的目录
    if not os.path.exists('temp'):
        os.makedirs('temp')
    
    # 获取当前本地时间
    from datetime import datetime
    now_local = datetime.now()
    
    print("=" * 60)
    print("🚀 Flask智能代码分析服务启动成功")
    print("=" * 60)
    print(f"📁 服务根目录: {os.path.abspath(HTML_FOLDER)}")
    print(f"🌐 访问地址: http://{LOCAL_IP}:5000")
    print(f"🤖 Ollama服务: {OLLAMA_CHAT_URL}")
    print(f"📊 模型: {OLLAMA_MODEL_NAME}")
    print(f"🕐 服务器时间: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📋 可用页面:")
    print(f"  1. 聊天界面: http://{LOCAL_IP}:5000/model-deployment.html")
    print(f"  2. 代码分析: http://{LOCAL_IP}:5000/code_analysis.html")
    print(f"  3. 自动分析仪表板: http://{LOCAL_IP}:5000/auto_analysis_dashboard.html")
    print()
    print("🎯 代码分析新规则:")
    print("  ✅ 有标签且有代码 -> 分析标记内的代码片段")
    print("  ⚠️ 有标签但无代码 -> 提示添加代码")
    print("  ❌ 无标签或不完整 -> 提示添加标签，不进行分析")
    print()
    print("🏷️ 标签使用方法:")
    print("  在代码中使用以下标记包围要分析的片段:")
    print("  #***start***#")
    print("  # 要分析的代码放在这里")
    print("  #***end***#")
    print()
    print("🔧 调试工具:")
    print(f"  POST {LOCAL_IP}:5000/api/debug/extract_test")
    print(f"  GET {LOCAL_IP}:5000/api/example/markers")
    print()
    print("✅ 所有其他功能保持不变")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)