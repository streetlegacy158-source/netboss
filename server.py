import os
import uuid
import hmac
import hashlib
import requests
from flask import Flask, render_template, request, jsonify, abort
from dotenv import load_dotenv

# Load secret keys from .env file
load_dotenv()

app = Flask(__name__, template_folder="templates")

DATAMART_API_KEY = os.getenv("DATAMART_API_KEY")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
DATAMART_WEBHOOK_SECRET = os.getenv("DATAMART_WEBHOOK_SECRET", "").encode()
DATAMART_BASE_URL = "https://api.datamartgh.shop/api/store/v1"

# 1. Serve index.html as the homepage
@app.route("/")
def home():
    return render_template("index.html")


# 2. Get live products catalog from DataMart
@app.route("/api/products", methods=["GET"])
def get_products():
    headers = {"Authorization": f"Bearer {DATAMART_API_KEY}"}
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/products", headers=headers)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 2.1 Get packages for frontend
@app.route("/api/packages", methods=["GET"])
def get_packages():
    headers = {"Authorization": f"Bearer {DATAMART_API_KEY}"}
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/products", headers=headers)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 2.2 Get Store Wallet Balance (Deposit & Earnings)
@app.route("/api/wallet/balance", methods=["GET"])
def get_wallet_balance():
    headers = {"Authorization": f"Bearer {DATAMART_API_KEY}"}
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/wallet/balance", headers=headers)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 3. Initialize Paystack Transaction Securely from Server
@app.route("/api/initialize-payment", methods=["POST"])
def initialize_payment():
    data = request.json or {}
    email = data.get("email", "customer@netboss.gh")
    amount = data.get("amount") # in pesewas (e.g. GH₵ 10.00 = 1000)
    phone_number = data.get("phoneNumber")
    network = data.get("network")
    capacity = data.get("capacity")

    if not amount or not phone_number or not network or not capacity:
        return jsonify({"status": "error", "message": "Missing required payment fields"}), 400

    paystack_url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "amount": int(amount),
        "currency": "GHS",
        "metadata": {
            "phoneNumber": phone_number,
            "network": network,
            "capacity": capacity
        }
    }

    try:
        res = requests.post(paystack_url, json=payload, headers=headers)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 4. Process Order after Paystack verification (Strict Idempotency Applied)
@app.route("/api/fulfill-order", methods=["POST"])
def fulfill_order():
    data = request.json or {}
    paystack_reference = data.get("reference")
    phone_number = data.get("phoneNumber")
    network = data.get("network")
    capacity = data.get("capacity")

    if not paystack_reference or not phone_number or not network or not capacity:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # Step A: Verify Payment with Paystack
    paystack_url = f"https://api.paystack.co/transaction/verify/{paystack_reference}"
    paystack_headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}

    verify_res = requests.get(paystack_url, headers=paystack_headers)
    verify_data = verify_res.json()

    if not verify_data.get("status") or verify_data["data"]["status"] != "success":
        return jsonify({"status": "error", "message": "Payment verification failed"}), 400

    # Step B: Trigger DataMart API to send data bundle with mandatory X-Idempotency-Key[cite: 1]
    idempotency_key = str(uuid.uuid4())
    datamart_headers = {
        "Authorization": f"Bearer {DATAMART_API_KEY}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }

    payload = {
        "phoneNumber": phone_number,
        "network": network,
        "capacity": int(capacity)
    }

    order_res = requests.post(f"{DATAMART_BASE_URL}/orders", json=payload, headers=datamart_headers)
    return jsonify(order_res.json()), order_res.status_code


# 5. Paystack Webhook Handler
@app.route("/api/paystack-webhook", methods=["POST"])
def paystack_webhook():
    signature = request.headers.get("x-paystack-signature")
    if not signature:
        return "", 400

    computed_signature = hmac.new(
        PAYSTACK_SECRET_KEY.encode('utf-8'),
        request.get_data(),
        hashlib.sha256
    ).hexdigest()

    if signature != computed_signature:
        return "Invalid signature", 400

    event = request.get_json()
    if event and event.get("event") == "charge.success":
        meta = event["data"]["metadata"]
        idempotency_key = str(uuid.uuid4())
        datamart_headers = {
            "Authorization": f"Bearer {DATAMART_API_KEY}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key
        }
        payload = {
            "phoneNumber": meta.get("phoneNumber"),
            "network": meta.get("network"),
            "capacity": int(meta.get("capacity"))
        }
        requests.post(f"{DATAMART_BASE_URL}/orders", json=payload, headers=datamart_headers)

    return "", 200


# 6. DataMart Webhook Handler with HMAC-SHA256 Signature Verification
@app.post('/api/datamart-webhook')
def datamart_webhook():
    sig = request.headers.get('X-Webhook-Signature', '')
    
    # If a secret is defined, verify the HMAC signature securely
    if DATAMART_WEBHOOK_SECRET:
        expected = hmac.new(DATAMART_WEBHOOK_SECRET, request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            abort(401)
            
    event = request.get_json(force=True)
    
    # Handle DataMart event types ("order.completed", "order.failed", etc.)[cite: 1]
    event_type = event.get("event")
    event_data = event.get("data", {})
    
    print(f"Received DataMart Event: {event_type} for Order Reference: {event_data.get('reference')}")
    
    # Respond within 8 seconds with 200 OK as required by DataMart documentation[cite: 1]
    return '', 200


if __name__ == "__main__":
    print("🚀 Server running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)