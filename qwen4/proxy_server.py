from flask import Flask, send_from_directory, request, jsonify, Response
import requests
from flask_cors import CORS
import socket
import time
from datetime import datetime, timedelta
import json
import traceback

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
OLLAMA_API_URL = f"http://{LOCAL_IP}:11435/api/chat"

# 打印服务启动信息
print("=== 服务启动成功 ===")
print(f"局域网IP：{LOCAL_IP}")
print(f"访问地址：http://{LOCAL_IP}:5000")
print(f"Ollama 转发地址：{OLLAMA_API_URL}")
print(f"连续对话配置：最多{MAX_HISTORY_ROUNDS}轮，{MAX_HISTORY_AGE}秒过期")
print("特性：自动让模型分点换行输出")
print("====================")

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

@app.route('/')
def serve_index():
    return send_from_directory(HTML_FOLDER, 'model-deployment.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口，用于监控服务状态"""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200

@app.route('/api/chat', methods=['POST'])
def proxy_chat():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        new_message = data.get("messages")[-1] if data.get("messages") else None

        # 参数校验
        if not user_id or not new_message or new_message.get("role") != "user":
            return jsonify({"error": "缺少 user_id 或用户消息"}), 400

        # 关键修改1：给用户的问题追加“分点输出”提示词
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)