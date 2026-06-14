from flask import Flask, request, jsonify, send_file
from flask_pymongo import PyMongo
import os
import redis
import json
import logging
from bson.objectid import ObjectId
from bson.errors import InvalidId
from time import time
from werkzeug.utils import secure_filename
import uuid
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIGURATION
# =========================

app = Flask(__name__)

# File upload configuration
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', './uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Create upload folder if not exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# =========================
# MONGODB CONFIG
# =========================

MONGO_USER = os.getenv("MONGO_USER", "")
MONGO_PASS = os.getenv("MONGO_PASS", "")
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_DB = os.getenv("MONGO_DB", "products")
MONGO_AUTH_SOURCE = os.getenv("MONGO_AUTH_SOURCE", "admin")

if MONGO_USER and MONGO_PASS:
    MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_DB}?authSource={MONGO_AUTH_SOURCE}"
else:
    MONGO_URI = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/{MONGO_DB}"

app.config["MONGO_URI"] = MONGO_URI

try:
    mongo = PyMongo(app)
    mongo.db.command('ping')
    print(f"✅ MongoDB connected to {MONGO_HOST}:{MONGO_PORT}/{MONGO_DB}")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    mongo = None

# =========================
# REDIS CONFIG (for caching)
# =========================

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

CACHE_TTL = int(os.getenv('CACHE_TTL', 300))
CACHE_ENABLED = os.getenv('CACHE_ENABLED', 'true').lower() == 'true'

try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2
    )
    redis_client.ping()
    print(f"✅ Redis connected to {REDIS_HOST}:{REDIS_PORT}")
    redis_available = True
except Exception as e:
    print(f"⚠️ Redis connection failed: {e} - caching disabled")
    redis_available = False
    redis_client = None

# =========================
# IMAGE UPLOAD ENDPOINT
# =========================

@app.route('/products/<product_id>/image', methods=['POST'])
def upload_product_image(product_id):
    """Upload an image for a product"""
    
    print(f"📸 Upload request for product: {product_id}")
    print(f"📁 Files in request: {request.files}")
    
    if mongo is None:
        return jsonify({'error': 'Database connection unavailable'}), 503
    
    # Check if product exists
    try:
        product = mongo.db.products.find_one({'_id': ObjectId(product_id)})
        if not product:
            print(f"❌ Product not found: {product_id}")
            return jsonify({'error': 'Product not found'}), 404
    except InvalidId:
        print(f"❌ Invalid product ID: {product_id}")
        return jsonify({'error': 'Invalid product ID'}), 400
    
    # Check if file was uploaded
    if 'image' not in request.files:
        print("❌ No 'image' field in request")
        return jsonify({'error': 'No image file provided'}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        print("❌ Empty filename")
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        print(f"❌ File type not allowed: {file.filename}")
        return jsonify({'error': f'File type not allowed. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400
    
    # Generate unique filename
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Save file
    file.save(filepath)
    print(f"✅ File saved: {filepath}")
    
    # Store image URL in database
    image_url = f"/api/products/images/{filename}"
    
    mongo.db.products.update_one(
        {'_id': ObjectId(product_id)},
        {'$set': {
            'image_url': image_url,
            'image_filename': filename,
            'image_updated_at': time()
        }}
    )
    
    # Invalidate cache
    if CACHE_ENABLED and redis_available and redis_client:
        cache_key = f'product:{product_id}'
        redis_client.delete(cache_key)
    
    print(f"✅ Image uploaded successfully for product {product_id}")
    
    return jsonify({
        'message': 'Image uploaded successfully',
        'product_id': product_id,
        'image_url': image_url
    }), 200

# =========================
# IMAGE SERVE ENDPOINTS
# =========================

@app.route('/api/products/images/<filename>', methods=['GET'])
def get_product_image_api(filename):
    """Serve product images - API endpoint (preferred)"""
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Image not found'}), 404
    
    return send_file(filepath, mimetype='image/jpeg')

@app.route('/products/images/<filename>', methods=['GET'])
def get_product_image(filename):
    """Serve product images - legacy endpoint"""
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Image not found'}), 404
    
    return send_file(filepath, mimetype='image/jpeg')

@app.route('/products/<product_id>/image', methods=['DELETE'])
def delete_product_image(product_id):
    """Delete product image"""
    
    if mongo is None:
        return jsonify({'error': 'Database connection unavailable'}), 503
    
    try:
        product = mongo.db.products.find_one({'_id': ObjectId(product_id)})
        if not product:
            return jsonify({'error': 'Product not found'}), 404
        
        filename = product.get('image_filename')
        if filename:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(filepath):
                os.remove(filepath)
        
        mongo.db.products.update_one(
            {'_id': ObjectId(product_id)},
            {'$unset': {'image_url': '', 'image_filename': '', 'image_updated_at': ''}}
        )
        
        if CACHE_ENABLED and redis_available and redis_client:
            cache_key = f'product:{product_id}'
            redis_client.delete(cache_key)
        
        return jsonify({'message': 'Image deleted successfully'}), 200
        
    except InvalidId:
        return jsonify({'error': 'Invalid product ID'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =========================
# HEALTH AND READINESS
# =========================

@app.route('/health', methods=['GET'])
def health():
    status = {
        "service": "product-service",
        "status": "UP",
        "timestamp": time()
    }
    
    if mongo is not None:
        try:
            mongo.db.command('ping')
            status["mongodb"] = "connected"
        except Exception as e:
            status["mongodb"] = f"error: {str(e)}"
            status["status"] = "DEGRADED"
    else:
        status["mongodb"] = "not_initialized"
        status["status"] = "DOWN"
    
    if redis_available and redis_client:
        try:
            redis_client.ping()
            status["redis"] = "connected"
        except Exception as e:
            status["redis"] = f"error: {str(e)}"
    else:
        status["redis"] = "disconnected"
    
    http_status = 200 if status["status"] == "UP" else 503
    return jsonify(status), http_status

@app.route('/ready', methods=['GET'])
def ready():
    if mongo is None:
        return jsonify({"ready": False, "reason": "MongoDB not initialized"}), 503
    
    try:
        mongo.db.command('ping')
        return jsonify({"ready": True}), 200
    except Exception as e:
        return jsonify({"ready": False, "reason": str(e)}), 503

# =========================
# PRODUCT CRUD ENDPOINTS
# =========================

@app.route('/products/<product_id>', methods=['GET'])
def get_product(product_id):
    """Get single product by ID with Redis caching"""
    
    if not product_id or len(product_id) != 24:
        return jsonify({"error": "Invalid product ID format (must be 24 hex chars)"}), 400
    
    cache_key = f'product:{product_id}'
    
    if CACHE_ENABLED and redis_available and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return jsonify(json.loads(cached))
        except Exception as e:
            pass
    
    if mongo is None:
        return jsonify({"error": "Database connection unavailable"}), 503
    
    try:
        product = mongo.db.products.find_one({'_id': ObjectId(product_id)})
    except InvalidId:
        return jsonify({"error": "Invalid product ID format"}), 400
    except Exception as e:
        return jsonify({"error": "Database error"}), 500
    
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    
    product['_id'] = str(product['_id'])
    
    if CACHE_ENABLED and redis_available and redis_client:
        try:
            redis_client.setex(cache_key, CACHE_TTL, json.dumps(product))
        except Exception as e:
            pass
    
    return jsonify(product)

@app.route('/products', methods=['GET'])
def list_products():
    """List all products with pagination"""
    
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        sort_by = request.args.get('sort_by', 'name')
        sort_order = request.args.get('sort_order', 'asc')
    except ValueError:
        return jsonify({"error": "Invalid pagination parameters"}), 400
    
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 20
    
    skip = (page - 1) * limit
    
    allowed_sort_fields = ['name', 'price', 'category', '_id']
    if sort_by not in allowed_sort_fields:
        sort_by = 'name'
    
    sort_direction = 1 if sort_order == 'asc' else -1
    
    if mongo is None:
        return jsonify({"error": "Database connection unavailable"}), 503
    
    try:
        total = mongo.db.products.count_documents({})
        
        cursor = mongo.db.products.find().sort(sort_by, sort_direction).skip(skip).limit(limit)
        products = []
        
        for p in cursor:
            p['_id'] = str(p['_id'])
            products.append(p)
        
        return jsonify({
            "data": products,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if total > 0 else 1
            }
        })
    except Exception as e:
        return jsonify({"error": "Failed to fetch products"}), 500

@app.route('/products', methods=['POST'])
def create_product():
    """Create a new product"""
    
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    required_fields = ['name', 'price']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    
    try:
        price = float(data['price'])
        if price <= 0:
            raise ValueError("Price must be positive")
    except (ValueError, TypeError):
        return jsonify({"error": "Price must be a positive number"}), 400
    
    product = {
        "name": data['name'],
        "price": price,
        "category": data.get('category', 'uncategorized'),
        "description": data.get('description', ''),
        "in_stock": data.get('in_stock', True),
        "created_at": time()
    }
    
    if mongo is None:
        return jsonify({"error": "Database connection unavailable"}), 503
    
    try:
        result = mongo.db.products.insert_one(product)
        product['_id'] = str(result.inserted_id)
        
        return jsonify(product), 201
    except Exception as e:
        return jsonify({"error": "Failed to create product"}), 500

@app.route('/products/<product_id>', methods=['PUT'])
def update_product(product_id):
    """Update an existing product"""
    
    if not product_id or len(product_id) != 24:
        return jsonify({"error": "Invalid product ID format"}), 400
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    if mongo is None:
        return jsonify({"error": "Database connection unavailable"}), 503
    
    try:
        data.pop('_id', None)
        data['updated_at'] = time()
        
        result = mongo.db.products.update_one(
            {'_id': ObjectId(product_id)},
            {'$set': data}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "Product not found"}), 404
        
        if CACHE_ENABLED and redis_available and redis_client:
            cache_key = f'product:{product_id}'
            redis_client.delete(cache_key)
        
        updated = mongo.db.products.find_one({'_id': ObjectId(product_id)})
        updated['_id'] = str(updated['_id'])
        
        return jsonify(updated), 200
        
    except InvalidId:
        return jsonify({"error": "Invalid product ID format"}), 400
    except Exception as e:
        return jsonify({"error": "Failed to update product"}), 500

@app.route('/products/<product_id>', methods=['DELETE'])
def delete_product(product_id):
    """Delete a product"""
    
    if not product_id or len(product_id) != 24:
        return jsonify({"error": "Invalid product ID format"}), 400
    
    if mongo is None:
        return jsonify({"error": "Database connection unavailable"}), 503
    
    try:
        # Delete image file if exists
        product = mongo.db.products.find_one({'_id': ObjectId(product_id)})
        if product and product.get('image_filename'):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], product['image_filename'])
            if os.path.exists(filepath):
                os.remove(filepath)
        
        result = mongo.db.products.delete_one({'_id': ObjectId(product_id)})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Product not found"}), 404
        
        if CACHE_ENABLED and redis_available and redis_client:
            cache_key = f'product:{product_id}'
            redis_client.delete(cache_key)
        
        return jsonify({"message": "Product deleted successfully"}), 200
        
    except InvalidId:
        return jsonify({"error": "Invalid product ID format"}), 400
    except Exception as e:
        return jsonify({"error": "Failed to delete product"}), 500

# =========================
# ERROR HANDLERS
# =========================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

# =========================
# START SERVER
# =========================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3001))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    
    print(f"\n{'='*50}")
    print(f"📦 Product Service Starting...")
    print(f"{'='*50}")
    print(f"📁 Upload folder: {os.path.abspath(UPLOAD_FOLDER)}")
    print(f"✅ Allowed extensions: {ALLOWED_EXTENSIONS}")
    print(f"✅ Max file size: {MAX_FILE_SIZE / 1024 / 1024}MB")
    print(f"🚀 Server running on port {port}")
    print(f"✅ Upload endpoint: POST /products/<id>/image")
    print(f"✅ Image endpoint: GET /api/products/images/<filename>")
    print(f"{'='*50}\n")
    
    app.run(host='0.0.0.0', port=port, debug=debug)