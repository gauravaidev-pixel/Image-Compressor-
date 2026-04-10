from flask import Flask, render_template_string, request, send_file
from PIL import Image
import io
import os

app = Flask(__name__)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Image Compressor - Size Between KB/MB</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 700px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
        }
        .upload-area {
            border: 3px dashed #667eea;
            border-radius: 15px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-bottom: 25px;
        }
        .upload-area:hover {
            border-color: #764ba2;
            background: #f8f9ff;
        }
        .upload-area.dragover {
            background: #f0f0ff;
            border-color: #764ba2;
        }
        .upload-icon {
            font-size: 50px;
            margin-bottom: 15px;
        }
        .file-input {
            display: none;
        }
        .size-inputs {
            display: flex;
            gap: 20px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }
        .input-group {
            flex: 1;
            min-width: 150px;
        }
        .input-group label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }
        .input-group input {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #ddd;
            border-radius: 10px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        .input-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        .input-group select {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #ddd;
            border-radius: 10px;
            font-size: 16px;
            background: white;
            cursor: pointer;
        }
        .compress-btn {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            margin-bottom: 25px;
        }
        .compress-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }
        .compress-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        .result-area {
            display: none;
            text-align: center;
            padding: 20px;
            background: #f8f9ff;
            border-radius: 15px;
        }
        .result-area.show {
            display: block;
        }
        .preview-image {
            max-width: 100%;
            max-height: 300px;
            border-radius: 10px;
            margin-bottom: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .file-info {
            margin: 15px 0;
            color: #555;
        }
        .download-btn {
            display: inline-block;
            padding: 12px 30px;
            background: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 25px;
            font-weight: bold;
            transition: background 0.3s;
        }
        .download-btn:hover {
            background: #45a049;
        }
        .error-msg {
            color: #f44336;
            margin-top: 10px;
            font-weight: 500;
        }
        .success-msg {
            color: #4CAF50;
            margin-top: 10px;
            font-weight: 500;
        }
        .selected-file {
            margin-top: 10px;
            color: #667eea;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🖼️ इमेज कंप्रेसर</h1>
        <p class="subtitle">अपनी पसंद के साइज़ रेंज में इमेज कंप्रेस करें (KB/MB)</p>
        
        <form id="uploadForm" enctype="multipart/form-data">
            <div class="upload-area" id="dropZone">
                <div class="upload-icon">📁</div>
                <p style="color: #666; margin-bottom: 10px;">इमेज अपलोड करने के लिए क्लिक करें या ड्रैग करें</p>
                <p style="color: #999; font-size: 14px;">JPG, PNG, WEBP, GIF सपोर्टेड</p>
                <input type="file" id="fileInput" name="image" accept="image/*" class="file-input">
                <div class="selected-file" id="fileName"></div>
            </div>

            <div class="size-inputs">
                <div class="input-group">
                    <label>न्यूनतम साइज़ (Min Size)</label>
                    <input type="number" id="minSize" value="100" min="1" step="0.1" required>
                </div>
                <div class="input-group">
                    <label>अधिकतम साइज़ (Max Size)</label>
                    <input type="number" id="maxSize" value="200" min="1" step="0.1" required>
                </div>
                <div class="input-group">
                    <label>यूनिट (Unit)</label>
                    <select id="unit">
                        <option value="KB" selected>KB (किलोबाइट)</option>
                        <option value="MB">MB (मेगाबाइट)</option>
                    </select>
                </div>
            </div>

            <button type="submit" class="compress-btn" id="compressBtn">कंप्रेस करें</button>
        </form>

        <div class="result-area" id="resultArea">
            <h3 style="margin-bottom: 15px; color: #333;">✅ कंप्रेस्ड इमेज</h3>
            <img id="previewImage" class="preview-image" src="" alt="Compressed Image">
            <div class="file-info">
                <p><strong>ओरिजिनल साइज़:</strong> <span id="originalSize">-</span></p>
                <p><strong>कंप्रेस्ड साइज़:</strong> <span id="compressedSize">-</span></p>
            </div>
            <a id="downloadBtn" class="download-btn" href="#" download="compressed_image.jpg">⬇️ डाउनलोड करें</a>
        </div>

        <div id="message"></div>
    </div>

    <script>
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const fileName = document.getElementById('fileName');
        const form = document.getElementById('uploadForm');
        const compressBtn = document.getElementById('compressBtn');
        const resultArea = document.getElementById('resultArea');
        const previewImage = document.getElementById('previewImage');
        const originalSize = document.getElementById('originalSize');
        const compressedSize = document.getElementById('compressedSize');
        const downloadBtn = document.getElementById('downloadBtn');
        const message = document.getElementById('message');

        // Click to upload
        dropZone.addEventListener('click', () => fileInput.click());

        // Drag and drop
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInput.files = files;
                updateFileName(files[0]);
            }
        });

        fileInput.addEventListener('change', (e) => {
            if (fileInput.files.length > 0) {
                updateFileName(fileInput.files[0]);
            }
        });

        function updateFileName(file) {
            const sizeKB = (file.size / 1024).toFixed(2);
            const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
            fileName.textContent = `📎 ${file.name} (${sizeKB} KB / ${sizeMB} MB)`;
        }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            if (!fileInput.files[0]) {
                showMessage('कृपया पहले एक इमेज चुनें!', 'error');
                return;
            }

            const minSize = parseFloat(document.getElementById('minSize').value);
            const maxSize = parseFloat(document.getElementById('maxSize').value);
            const unit = document.getElementById('unit').value;

            if (minSize >= maxSize) {
                showMessage('न्यूनतम साइज़, अधिकतम साइज़ से कम होना चाहिए!', 'error');
                return;
            }

            compressBtn.disabled = true;
            compressBtn.textContent = 'कंप्रेस हो रहा है...';
            message.innerHTML = '';

            const formData = new FormData();
            formData.append('image', fileInput.files[0]);
            formData.append('min_size', minSize);
            formData.append('max_size', maxSize);
            formData.append('unit', unit);

            try {
                const response = await fetch('/compress', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (response.ok) {
                    previewImage.src = data.image_url;
                    originalSize.textContent = data.original_size;
                    compressedSize.textContent = data.compressed_size;
                    downloadBtn.href = data.image_url;
                    
                    const ext = fileInput.files[0].name.split('.').pop();
                    downloadBtn.download = `compressed_${Date.now()}.${ext}`;
                    
                    resultArea.classList.add('show');
                    showMessage('✅ इमेज सफलतापूर्वक कंप्रेस हो गई!', 'success');
                } else {
                    showMessage(data.error || 'कंप्रेशन में त्रुटि हुई!', 'error');
                }
            } catch (error) {
                showMessage('सर्वर से कनेक्ट नहीं हो पाया!', 'error');
            } finally {
                compressBtn.disabled = false;
                compressBtn.textContent = 'कंप्रेस करें';
            }
        });

        function showMessage(msg, type) {
            message.innerHTML = `<div class="${type}-msg">${msg}</div>`;
        }
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/compress', methods=['POST'])
def compress_image():
    try:
        # Get uploaded file
        if 'image' not in request.files:
            return {'error': 'कोई इमेज अपलोड नहीं की गई'}, 400
        
        file = request.files['image']
        if file.filename == '':
            return {'error': 'कोई फाइल नहीं चुनी गई'}, 400

        # Get size parameters
        min_size = float(request.form.get('min_size', 100))
        max_size = float(request.form.get('max_size', 200))
        unit = request.form.get('unit', 'KB')
        
        # Convert to bytes
        multiplier = 1024 if unit == 'KB' else 1024 * 1024
        min_bytes = min_size * multiplier
        max_bytes = max_size * multiplier

        # Read original image
        img = Image.open(file)
        original_format = img.format
        original_size = len(file.read())
        file.seek(0)

        # Convert RGBA to RGB if needed
        if img.mode == 'RGBA' and original_format == 'JPEG':
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3])
            img = rgb_img
        elif img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        # Binary search for optimal quality
        quality = 90
        low, high = 1, 95
        best_img_data = None
        best_size = 0
        
        while low <= high:
            quality = (low + high) // 2
            
            # Compress with current quality
            img_io = io.BytesIO()
            
            if original_format == 'JPEG' or img.mode == 'RGB':
                img.save(img_io, format='JPEG', quality=quality, optimize=True)
            elif original_format == 'PNG':
                img.save(img_io, format='PNG', optimize=True, compress_level=9 - (quality // 10))
            else:
                img.save(img_io, format='JPEG', quality=quality, optimize=True)
            
            current_size = img_io.tell()
            
            if min_bytes <= current_size <= max_bytes:
                best_img_data = img_io.getvalue()
                best_size = current_size
                break
            elif current_size < min_bytes:
                if quality < 95:
                    low = quality + 1
                else:
                    best_img_data = img_io.getvalue()
                    best_size = current_size
                    break
            else:
                high = quality - 1
                
            if best_img_data is None or abs(current_size - ((min_bytes + max_bytes) // 2)) < abs(best_size - ((min_bytes + max_bytes) // 2)):
                best_img_data = img_io.getvalue()
                best_size = current_size

        # If we couldn't get within range, use best attempt
        if best_img_data is None:
            img_io = io.BytesIO()
            img.save(img_io, format='JPEG', quality=85, optimize=True)
            best_img_data = img_io.getvalue()
            best_size = len(best_img_data)

        # Create response
        import base64
        img_base64 = base64.b64encode(best_img_data).decode('utf-8')
        
        return {
            'image_url': f'data:image/jpeg;base64,{img_base64}',
            'original_size': format_size(original_size),
            'compressed_size': format_size(best_size),
            'original_bytes': original_size,
            'compressed_bytes': best_size
        }
        
    except Exception as e:
        return {'error': f'कंप्रेशन में त्रुटि: {str(e)}'}, 500

def format_size(bytes):
    if bytes < 1024:
        return f"{bytes} B"
    elif bytes < 1024 * 1024:
        return f"{bytes / 1024:.2f} KB"
    else:
        return f"{bytes / (1024 * 1024):.2f} MB"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
