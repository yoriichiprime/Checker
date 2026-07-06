from http.server import BaseHTTPRequestHandler
import json
import re
import os
import requests
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_123")

def luhn_check(card_number):
    digits = [int(d) for d in str(card_number)]
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0

def get_card_brand(card_number):
    card_number = str(card_number)
    if re.match(r'^4[0-9]{12}(?:[0-9]{3})?$', card_number): return "Visa"
    if re.match(r'^5[1-5][0-9]{14}$', card_number): return "Mastercard"
    if re.match(r'^3[47][0-9]{13}$', card_number): return "Amex"
    if re.match(r'^6(?:011|5[0-9]{2})[0-9]{12}$', card_number): return "Discover"
    return "Unknown"

def bin_lookup(bin_number):
    try:
        resp = requests.get(f"https://lookup.binlist.net/{bin_number}", headers={"Accept-Version": "3"}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {"bank": data.get("bank", {}).get("name", "Unknown"), "country": data.get("country", {}).get("name", "Unknown"), "type": data.get("type", "Unknown"), "brand": data.get("scheme", "Unknown")}
    except:
        pass
    return {"bank": "Unknown", "country": "Unknown", "type": "Unknown", "brand": "Unknown"}

def validate_card_pattern(card_number):
    card_number = str(card_number).replace(" ", "").replace("-", "")
    if not card_number.isdigit(): return {"valid_pattern": False, "error": "Digits only"}
    if len(card_number) < 13 or len(card_number) > 19: return {"valid_pattern": False, "error": "Invalid length"}
    if not luhn_check(card_number): return {"valid_pattern": False, "error": "Failed Luhn check"}
    brand = get_card_brand(card_number)
    if brand == "Unknown": return {"valid_pattern": False, "error": "Unknown brand"}
    bin_info = bin_lookup(card_number[:6])
    return {"valid_pattern": True, "brand": brand, "bin": card_number[:6], "last4": card_number[-4:], "bin_info": bin_info}

DECLINE_MAP = {
    "insufficient_funds": "Insufficient Balance", "card_declined": "Card Declined", "expired_card": "Card Expired",
    "incorrect_cvc": "Invalid CVC", "incorrect_number": "Invalid Number", "fraudulent": "Fraud Flag",
    "do_not_honor": "Do Not Honor", "generic_decline": "Generic Decline", "lost_card": "Lost Card",
    "stolen_card": "Stolen Card", "pickup_card": "Pickup Card", "restricted_card": "Restricted Card"
}

def micro_transaction_check(card_number, exp_month, exp_year, cvc):
    try:
        token = stripe.Token.create(card={"number": card_number, "exp_month": int(exp_month), "exp_year": int(exp_year), "cvc": str(cvc)})
        charge = stripe.Charge.create(amount=100, currency="usd", source=token.id, capture=False, description="Card verification")
        stripe.Refund.create(charge=charge.id)
        return {"transaction_status": "approved", "decline_code": None, "decline_reason": None, "message": "Valid with funds"}
    except stripe.error.CardError as e:
        code = e.error.decline_code or "generic_decline"
        reason = DECLINE_MAP.get(code, str(e.error.message))
        return {"transaction_status": "declined", "decline_code": code, "decline_reason": reason, "message": e.error.message}
    except Exception as e:
        return {"transaction_status": "error", "decline_reason": str(e), "message": "Gateway error"}

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            card_number = str(data.get('card_number', '')).replace(" ", "").replace("-", "")
            exp_month = str(data.get('exp_month', ''))
            exp_year = str(data.get('exp_year', ''))
            cvc = str(data.get('cvc', ''))
            
            if not card_number or not exp_month or not exp_year or not cvc:
                self.send_response(400); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
                self.wfile.write(json.dumps({"error": "card_number, exp_month, exp_year, cvc required"}).encode()); return
            
            pattern = validate_card_pattern(card_number)
            if not pattern["valid_pattern"]:
                self.send_response(400); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
                self.wfile.write(json.dumps({"step": "pattern_validation", "pattern_validation": pattern, "transaction_check": None}).encode()); return
            
            transaction = micro_transaction_check(card_number, exp_month, exp_year, cvc)
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
            self.wfile.write(json.dumps({"step": "completed", "pattern_validation": pattern, "transaction_check": transaction}).encode())
        except json.JSONDecodeError:
            self.send_response(400); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
        except Exception as e:
            self.send_response(500); self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def do_OPTIONS(self):
        self.send_response(200); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS'); self.send_header('Access-Control-Allow-Headers', 'Content-Type'); self.end_headers()
