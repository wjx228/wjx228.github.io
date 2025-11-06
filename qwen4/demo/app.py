from flask import Flask, request, jsonify
import subprocess
from flask_cors import CORS
CORS(app)  # 允许跨域请求

app = Flask(__name__)

@app.route('/ask', methods=['POST'])
def ask():
    user_input = request.json.get('question')

    # 使用 subprocess 运行 ollama 命令并获取模型回答
    try:
        result = subprocess.run(
            ['ollama', 'run', 'qwen:7b-chat-q4_0', '--input', user_input],
            capture_output=True, text=True, check=True
        )
        answer = result.stdout.strip()  # 获取模型的输出

        return jsonify({'answer': answer})

    except subprocess.CalledProcessError as e:
        return jsonify({'error': '模型运行出错', 'details': e.stderr}), 500

if __name__ == '__main__':
    app.run(debug=True)
