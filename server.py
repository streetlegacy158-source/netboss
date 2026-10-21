import os
import uuid
import hmac
import hashlib
import requests
from flask import Flask, render_template, request, jsonify, abort
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates")

# Environment Variables
DATAMART_API_KEY = os.getenv("DATAMART_API_KEY")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
DATAMART_WEBHOOK_SECRET = os.getenv("DATAMART_WEBHOOK_SECRET", "").encode()
ADMIN_PASSCODE = os.getenv("ADMIN_PASSCODE", "netboss2026") # Set ADMIN_PASSCODE in Render env or use default
DATAMART_BASE_URL = "https://api.datamartgh.shop/api/store/v1"

# Store Configuration & Contact info
STORE_INFO = {
    "email": "netboss338@gmail.com",
    "whatsappChannel": "https://whatsapp.com/channel/0029VbETzfPElagtfvSGC91G"
}

def get_datamart_headers(idempotency_key=None):
    headers = {
        "Authorization": f"Bearer {DATAMART_API_KEY}",
        "Content-Type": "application/json"
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


# --- FRONTEND ROUTES ---

@app.route("/")
def home():
    return render_template("index.html", store=STORE_INFO, paystack_public_key=PAYSTACK_PUBLIC_KEY)

@app.route("/admin")
def admin_page():
    return render_template("admin.html", store=STORE_INFO)


# --- ADMIN AUTHENTICATION API ---

@app.route("/api/admin/verify", methods=["POST"])
def verify_admin():
    data = request.json or {}
    passcode = data.get("passcode")
    if passcode == ADMIN_PASSCODE:
        return jsonify({"status": "success", "message": "Authenticated"})
    return jsonify({"status": "error", "message": "Invalid admin passcode"}), 401


# --- DATAMART PROXY ENDPOINTS ---

# Get Wallet Balance
@app.route("/api/wallet/balance", methods=["GET"])
def get_wallet_balance():
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/wallet/balance", headers=get_datamart_headers())
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Get Wallet Transactions
@app.route("/api/wallet/transactions", methods=["GET"])
def get_wallet_transactions():
    limit = request.args.get("limit", "20")
    page = request.args.get("page", "1")
    tx_type = request.args.get("type", "")
    url = f"{DATAMART_BASE_URL}/wallet/transactions?page={page}&limit={limit}"
    if tx_type:
        url += f"&type={tx_type}"
    try:
        res = requests.get(url, headers=get_datamart_headers())
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Get Products Catalog
@app.route("/api/products", methods=["GET"])
def get_products():
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/products", headers=get_datamart_headers())
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Get Paginated Orders (For Admin & Search)
@app.route("/api/orders", methods=["GET"])
def get_orders():
    status = request.args.get("status", "")
    limit = request.args.get("limit", "50")
    page = request.args.get("page", "1")
    url = f"{DATAMART_BASE_URL}/orders?page={page}&limit={limit}"
    if status:
        url += f"&status={status}"
    try:
        res = requests.get(url, headers=get_datamart_headers())
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Get Single Order by Reference
@app.route("/api/orders/<reference>", methods=["GET"])
def get_single_order(reference):
    try:
        res = requests.get(f"{DATAMART_BASE_URL}/orders/{reference}", headers=get_datamart_headers())
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- PAYMENT CHECKOUT & FULFILLMENT ---

@app.route("/api/initialize-payment", methods=["POST"])
def initialize_payment():
    data = request.json or {}
    email = data.get("email", STORE_INFO["email"])
    amount = data.get("amount")  # in pesewas
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


@app.route("/api/fulfill-order", methods=["POST"])
def fulfill_order():
    data = request.json or {}
    paystack_reference = data.get("reference")
    phone_number = data.get("phoneNumber")
    network = data.get("network")
    capacity = data.get("capacity")

    if not paystack_reference or not phone_number or not network or not capacity:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # Verify Payment with Paystack
    paystack_url = f"https://api.paystack.co/transaction/verify/{paystack_reference}"
    paystack_headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
    verify_res = requests.get(paystack_url, headers=paystack_headers)
    verify_data = verify_res.json()

    if not verify_data.get("status") or verify_data["data"]["status"] != "success":
        return jsonify({"status": "error", "message": "Payment verification failed"}), 400

    # Trigger DataMart Order with mandatory X-Idempotency-Key
    idempotency_key = str(uuid.uuid4())
    payload = {
        "phoneNumber": phone_number,
        "network": network,
        "capacity": int(capacity)
    }

    order_res = requests.post(
        f"{DATAMART_BASE_URL}/orders", 
        json=payload, 
        headers=get_datamart_headers(idempotency_key)
    )
    return jsonify(order_res.json()), order_res.status_code


# --- WEBHOOKS ---

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
        payload = {
            "phoneNumber": meta.get("phoneNumber"),
            "network": meta.get("network"),
            "capacity": int(meta.get("capacity"))
        }
        requests.post(
            f"{DATAMART_BASE_URL}/orders", 
            json=payload, 
            headers=get_datamart_headers(idempotency_key)
        )

    return "", 200


@app.post('/api/datamart-webhook')
def datamart_webhook():
    sig = request.headers.get('X-Webhook-Signature', '')
    if DATAMART_WEBHOOK_SECRET:
        expected = hmac.new(DATAMART_WEBHOOK_SECRET, request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            abort(401)
            
    event = request.get_json(force=True)
    print(f"DataMart Webhook Event Received: {event.get('event')}")
    return '', 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)