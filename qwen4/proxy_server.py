from flask import Flask, send_from_directory, request, jsonify
import requests

app = Flask(__name__)

# 配置：Ollama API 地址（就是你之前启动的 11435 端口）
OLLAMA_API_URL = "http://127.0.0.1:11435/api/chat"
# 配置：网页文件所在文件夹（当前脚本和 index.html 在同一目录，填 "." 即可）
HTML_FOLDER = "."

# 访问 http://IP:5000 时，返回网页
@app.route('/')
def serve_index():
    return send_from_directory(HTML_FOLDER, 'model-deployment.html')

# 网页里的 API 请求，转发到 Ollama API
@app.route('/api/chat', methods=['POST'])
def proxy_chat():
    try:
        # 接收网页的请求数据
        data = request.get_json()
        # 转发到本地 Ollama API
        response = requests.post(OLLAMA_API_URL, json=data, stream=True)
        # 把 Ollama 的响应（流式/普通）原封不动返回给网页
        return response.iter_content(chunk_size=1024), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 关键：0.0.0.0 允许局域网访问，端口 5000（可改成你想要的端口，比如 8080）
    app.run(host='0.0.0.0', port=5000, debug=False)