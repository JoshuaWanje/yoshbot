#Nairobi & Kiambu Movers chatbot — chats to collect trip details, then quotes a price.
# Uses OpenRouter's standard chat/completions endpoint directly (no Anthropic SDK needed).

import json, os, sys
import requests
from flask import Flask, request, jsonify, send_from_directory
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads OPENROUTER_API_KEY from .env if present
except ImportError:
    pass

API_KEY = os.environ.get("OPENROUTER_API_KEY")
MODEL = "anthropic/claude-sonnet-4.6"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Gmail SMTP sends the branded quote email to the customer — free, uses your
# own Gmail address (with an App Password, not your normal password).
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")
FROM_EMAIL = os.environ.get("FROM_EMAIL", f"Nairobi & Kiambu Movers <{SMTP_EMAIL}>")
COMPANY_PHONE = os.environ.get("COMPANY_PHONE", "+254 XXX XXX XXX")
COMPANY_EMAIL = os.environ.get("COMPANY_EMAIL", "quotes@yourdomain.com")


# ============================================================
# TASK 3 ELISHA (Pricing & Quote) — pricing constants
# ============================================================
BASE_FEE = {"Bedsitter": 3000, "1 Bedroom": 5000, "2 Bedroom": 8000, "3 Bedroom": 12000, "4 Bedroom": 16000}
PER_KM, PER_SEAT, MINIMUM = 100, 300, 3500


# ============================================================
# TASK 1 (AI & Prompt Engineering)
# write the full system prompt and implement ask_ai().
# ============================================================
SYSTEM_PROMPT = """You are a helpful Nairobi and Kiambu movers assistant.

LOCATION RESTRICTION:
You only provide moving services within Nairobi County and Kiambu County, Kenya.

If the user's pickup or destination is outside Nairobi County or Kiambu County:
- Do not proceed with the booking.
- Tell the user that the location is outside our service area.
- If the county is known, mention it.
- For example:
  "[Location] is located in [County] and is outside our Nairobi/Kiambu service area. We currently only provide moving services within Nairobi and Kiambu County."
Your job is to gather the information needed for a moving quote, one field at a time.
Ask only for the next missing detail and do not request multiple pieces of information in the same message.

Follow this order strictly:
1. Ask for the pickup location.
2. Ask for the destination.
3. Ask for the house type.
4. Ask for the number of seats owned.

Rules:
- Keep replies short, friendly, and conversational.
- If the user gives more than one item at once, acknowledge the information received and ask only for the missing field.
- Accept common variations for house types such as Bedsitter, 1 Bedroom, 2 Bedroom, 3 Bedroom, and 4 Bedroom.
- Treat seats owned as an integer number.
- Once all four details are known, respond with a brief confirmation and then end the message with a line exactly in this format:
DATA_READY: {"pickup_location": "...", "destination": "...", "house_type": "...", "seats_owned": 0}
- Do not add extra JSON blocks or markdown fences.
- The final line must be valid JSON and include all collected details.
"""


def ask_ai(history):
    """Send the conversation to OpenRouter and return the model reply text."""
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost",
        "X-Title": "Nairobi Movers Chatbot",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "temperature": 0.2,
        "max_tokens": 150,
    }

    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


# ============================================================
# TASK 2 (Location & Distance)
 # This replaces geocode() and driving_km() in movers.py.
# Covers suggestions 1-4 from the task guide:
#  1. Bias results toward Nairobi/Kiambu with a viewbox
#  2. Cache repeated lookups
#  3. Handle Nominatim/OSRM rate limits gracefully (retry once after a short delay)
#  4. Verify the result is actually within Nairobi/Kiambu (check address components)
import time

_geocode_cache = {}
# Rough bounding box covering Nairobi + Kiambu counties (min_lon, min_lat, max_lon, max_lat)
_NAIROBI_KIAMBU_VIEWBOX = "36.60,-1.45,37.10,-0.95"

# Counties Nominatim should report back, to confirm the match is actually local
_VALID_COUNTIES = {"nairobi", "kiambu"}


def geocode(place):
    """Find a location in Nairobi or Kiambu using Nominatim."""

    key = place.strip().lower()

    if key in _geocode_cache:
        return _geocode_cache[key]

    params = {
        "format": "jsonv2",
        "limit": 5,
        "countrycodes": "ke",
        "viewbox": _NAIROBI_KIAMBU_VIEWBOX,
        "bounded": 0,  # Bias toward Nairobi/Kiambu but don't block valid areas
        "addressdetails": 1,
        "q": f"{place}, Kenya",
    }

    headers = {
        "User-Agent": "Nairobi-Kiambu-Movers/1.0"
    }

    results = []

    for attempt in range(2):
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params=params,
                headers=headers,
                timeout=10
            )

            if resp.status_code == 429 and attempt == 0:
                time.sleep(2)
                continue

            resp.raise_for_status()
            results = resp.json()
            break

        except requests.RequestException:
            if attempt == 0:
                time.sleep(2)
                continue
            raise

    if not results:
        raise ValueError(f"Location not found: {place}")

    # Look through all returned results instead of only results[0]
    for match in results:

        address = match.get("address", {})

        # Check the important location fields
        location_text = " ".join([
            str(address.get("county", "")),
            str(address.get("state", "")),
            str(address.get("city", "")),
            str(address.get("town", "")),
            str(address.get("municipality", "")),
            str(address.get("district", "")),
            str(address.get("city_district", "")),
        ]).lower()

        # Accept locations identified as Nairobi or Kiambu
        if "nairobi" in location_text or "kiambu" in location_text:

            result = (
                float(match["lat"]),
                float(match["lon"])
            )

            _geocode_cache[key] = result
            return result

    # If Nominatim did not provide county information,
    # use the Nairobi/Kiambu geographical area as a fallback.
    for match in results:

        lat = float(match["lat"])
        lon = float(match["lon"])

        # Broad Nairobi + Kiambu area
        if (
            -1.55 <= lat <= -0.75
            and 36.55 <= lon <= 37.15
        ):
            result = (lat, lon)

            _geocode_cache[key] = result
            return result

    raise ValueError(
        f"'{place}' could not be confirmed as being in Nairobi or Kiambu County."
    )

    

def driving_km(a, b):
    """Return driving distance in km between two (lat, lon) tuples, using OSRM."""
    url = f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"

    for attempt in range(2):  # try once, then retry once if rate-limited
        resp = requests.get(url, params={"overview": "false"}, timeout=10)
        if resp.status_code == 429 and attempt == 0:
            time.sleep(2)
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        data = {}

    if not data.get("routes"):
        raise ValueError("No route found.")
    return data["routes"][0]["distance"] / 1000


# ============================================================
# TASK 3 ELISHA(Pricing & Quote) — quote logic
## Pricing — placeholder rates, edit to match your real rate card.
BASE_FEE = {"Bedsitter": 3000, "1 Bedroom": 5000, "2 Bedroom": 8000, "3 Bedroom": 12000, "4 Bedroom": 16000}
PER_KM, PER_SEAT, MINIMUM = 100, 300, 3500


def quote(data):
    # Geocode, get distance, compute price, print + save. All errors caught here.
    try:
        km = driving_km(geocode(data["pickup_location"]), geocode(data["destination"]))
    except (requests.RequestException, ValueError) as e:
        print(f"Could not calculate distance: {e}")
        return

    base = BASE_FEE.get(data["house_type"], BASE_FEE["1 Bedroom"])
    dist_charge, seat_charge = round(km * PER_KM), data["seats_owned"] * PER_SEAT
    total = max(base + dist_charge + seat_charge, MINIMUM)

    lines = [f"Distance: {km:.1f} km", f"Base fee ({data['house_type']}): KES {base:,}",
              f"Distance charge: KES {dist_charge:,}", f"Seats charge: KES {seat_charge:,}", f"TOTAL: KES {total:,}"]
    print("\n" + "\n".join(lines))
    with open("movers_quote.txt", "w", encoding="utf-8") as f:
        f.write(f"{data['pickup_location']} -> {data['destination']} | {data['house_type']}, {data['seats_owned']} seats\n")
        f.write("\n".join(lines))
    print("Saved to movers_quote.txt")


# ============================================================
# CUSTOMER EMAIL — branded quote email sent via Resend after the
# contact form is submitted.
# ============================================================
def build_quote_email_html(lead):
    """Build the branded HTML quote email for a lead. lead["quote"] holds the breakdown."""
    q = lead["quote"]
    name = lead["name"]

    def row(label, value, bold=False):
        weight = "font-weight:700;" if bold else ""
        return f"""
        <tr>
          <td style="padding:6px 0;color:#334155;font-size:14px;{weight}">{label}</td>
          <td style="padding:6px 0;color:#0f172a;font-size:14px;text-align:right;{weight}">{value}</td>
        </tr>"""
    
    rows = "".join([
        row("Distance", f'{q["km"]} km'),
        row("House", q["house_type"]),
        row("Base fee", f'KES {q["base"]:,}'),
        row("Distance charge", f'KES {q["dist_charge"]:,}'),
        row("Seats charge", f'KES {q["seat_charge"]:,}'),
    ])

    return f"""<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin:0;padding:0;">
    <div style="background:#f1f5f9;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;">
      <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;
                  box-shadow:0 1px 3px rgba(0,0,0,0.1);">

        <div style="padding:22px 26px 16px;border-bottom:1px solid #e2e8f0;">
          <div style="font-size:17px;font-weight:700;color:#2563eb;">
            &#128666; Nairobi &amp; Kiambu Movers
          </div>
          <div style="font-size:13px;color:#64748b;margin-top:2px;">Your moving quote</div>
        </div>

        <div style="padding:22px 26px 6px;">
          <p style="font-size:15px;color:#0f172a;margin:0 0 6px;">Hi <strong>{name}</strong>,</p>
          <p style="font-size:15px;color:#0f172a;margin:0 0 18px;">Thank you for requesting a moving quote.</p>

          <table style="width:100%;border-collapse:collapse;background:#eff6ff;border-radius:10px;
                        padding:14px 16px;" cellpadding="0" cellspacing="0">
            <tr><td colspan="2" style="padding:8px 12px 0;">
              <table style="width:100%;border-collapse:collapse;">
                {rows}
                <tr><td colspan="2" style="border-top:1px solid #bfdbfe;padding-top:10px;"></td></tr>
                <tr>
                  <td style="padding:6px 0 10px;font-size:15px;font-weight:700;color:#0f172a;">TOTAL</td>
                  <td style="padding:6px 0 10px;font-size:17px;font-weight:800;color:#2563eb;text-align:right;">
                    KES {q["total"]:,}
                  </td>
                </tr>
              </table>
            </td></tr>
          </table>

          <p style="font-size:14px;color:#0f172a;margin:20px 0 12px;">
            Reply to this email or call us to confirm your booking.
          </p>

          <div style="background:#ecfdf5;border-radius:10px;padding:12px 16px;font-size:14px;color:#065f46;">
            &#128222; {COMPANY_PHONE}<br>
            &#9993; {COMPANY_EMAIL}
          </div>

          <p style="font-size:14px;color:#0f172a;margin:20px 0 4px;">We look forward to helping with your move.</p>
        </div>

        <div style="padding:14px 26px 20px;font-size:11px;color:#94a3b8;">
          {q["pickup_location"]} &rarr; {q["destination"]}
        </div>
      </div>
    </div>
    </body>
    </html>
    """


def send_quote_email(lead):
    """Send the branded quote email to the customer via Gmail SMTP. Returns True/False."""
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        print("SMTP_EMAIL / SMTP_APP_PASSWORD not set — skipping email send.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Nairobi & Kiambu Movers quote"
    msg["From"] = FROM_EMAIL
    msg["To"] = lead["email"]
    msg.attach(MIMEText(build_quote_email_html(lead), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_EMAIL, lead["email"], msg.as_string())
        return True
    except smtplib.SMTPException as e:
        print(f"Could not send quote email: {e}")
        return False


# ============================================================
# WEB SERVER — lets movers_chat.html talk to this backend instead of
# calling OpenRouter/Nominatim/OSRM directly from the browser. This keeps
# OPENROUTER_API_KEY on the server, and saves leads/quotes to disk here
# instead of the browser's localStorage.
# ============================================================
app = Flask(__name__)


@app.route("/")
def serve_frontend():
    """Serve movers_chat.html — expected to sit in the same folder as this file."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "movers_chat.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Body: {"history": [{"role": "...", "content": "..."}, ...]} -> {"reply": "..."}"""
    body = request.get_json(force=True) or {}
    history = body.get("history", [])
    try:
        reply = ask_ai(history)
        return jsonify({"reply": reply})
    except requests.HTTPError as e:
        return jsonify({"error": f"API error ({e.response.status_code}): {e.response.text[:200]}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"Connection problem: {e}"}), 502
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quote", methods=["POST"])
def api_quote():
    """Body: {"pickup_location", "destination", "house_type", "seats_owned"} -> full quote breakdown."""
    data = request.get_json(force=True) or {}
    required = ["pickup_location", "destination", "house_type", "seats_owned"]
    missing = [k for k in required if k not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        km = driving_km(geocode(data["pickup_location"]), geocode(data["destination"]))
    except (requests.RequestException, ValueError) as e:
        return jsonify({"error": f"Could not calculate distance: {e}"}), 400

    base = BASE_FEE.get(data["house_type"], BASE_FEE["1 Bedroom"])
    dist_charge = round(km * PER_KM)
    seat_charge = data["seats_owned"] * PER_SEAT
    total = max(base + dist_charge + seat_charge, MINIMUM)

    # Reuse the existing quote() function so movers_quote.txt still gets written.
    quote(data)

    return jsonify({
        "pickup_location": data["pickup_location"],
        "destination": data["destination"],
        "house_type": data["house_type"],
        "seats_owned": data["seats_owned"],
        "km": round(km, 1),
        "base": base,
        "dist_charge": dist_charge,
        "seat_charge": seat_charge,
        "total": total,
    })


@app.route("/api/lead", methods=["POST"])
def api_lead():
    """Body: {"name", "phone", "email", "quote": {...}} -> appended to movers_leads.jsonl, emails the quote."""
    lead = request.get_json(force=True) or {}
    for field in ("name", "phone", "email"):
        if not lead.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    if not lead.get("quote"):
        return jsonify({"error": "Missing field: quote"}), 400

    with open("movers_leads.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(lead) + "\n")

    email_sent = send_quote_email(lead)
    return jsonify({"status": "ok", "email_sent": email_sent})


def run_web():
    """Start the Flask dev server."""
    if not API_KEY:
        sys.exit("Set OPENROUTER_API_KEY in a .env file or environment variable.")
    app.run(debug=True, port=5000)


# ============================================================
# TASK 4 (Chat Loop & Error Handling)
#  Covers suggestions from the task guide:
#  1. "quit"/"exit" command
#  2. "restart" command
#  3. Conversation length limit (avoid runaway loops / API costs)
#  4. Better JSON error recovery (ask the AI to resend instead of giving up)
# ============================================================
MAX_TURNS = 50
# ============================================================
def chat():
    """Run the interactive chat loop that collects details and triggers a quote."""
    history = [{"role": "assistant", "content": "Hi! I can help you get a moving quote in Nairobi or Kiambu. Where are you moving from?"}]
    print(f"Bot: {history[0]['content']}")
    turns = 0

    while True:
        user_text = input("You: ").strip()

        if user_text.lower() in ("quit", "exit"):
            print("Bot: Okay, ending the chat. Have a good day!")
            return

        if user_text.lower() == "restart":
            history = [{"role": "assistant", "content": "Sure, let's start over. Where are you moving from?"}]
            print(f"Bot: {history[0]['content']}")
            turns = 0
            continue

        if not user_text:
            print("Bot: Please type something.")
            continue

        turns += 1
        if turns > MAX_TURNS:
            print("Bot: This conversation is getting long — let's restart to keep things on track.")
            history = [{"role": "assistant", "content": "Where are you moving from?"}]
            turns = 0
            continue

        history.append({"role": "user", "content": user_text})

        try:
            reply = ask_ai(history)
        except requests.HTTPError as e:
            print(f"Bot: API error ({e.response.status_code}): {e.response.text[:200]}")
            continue
        except requests.RequestException as e:
            print(f"Bot: Connection problem ({e}). Try again.")
            continue

        history.append({"role": "assistant", "content": reply})

        if "DATA_READY:" in reply:
            visible, json_part = reply.split("DATA_READY:", 1)
            if visible.strip():
                print(f"Bot: {visible.strip()}")
            try:
                quote(json.loads(json_part.strip()))
                return
            except json.JSONDecodeError:
                # Ask the AI to resend the data instead of giving up entirely
                history.append({"role": "user",
                                 "content": "That JSON didn't come through correctly. Please resend the DATA_READY line with valid JSON."})
                print("Bot: Sorry, something went wrong reading the details-let me try that again.")
                continue
            
            print(f"Bot: {reply}")
            
def main():
            """"Entry point: 'python movers.py' runs the CLI chat, 'python movers.py web' starts the Flask server."""
            if not API_KEY:
                sys.exit("Set OPENROUTER_API_KEY in a .env file or environment variable.")
                
            if len(sys.argv) > 1 and sys.argv[1] == "web":
                run_web()
                return
            try:
                chat()
            except KeyboardInterrupt:
                print("\nCancelled.")
                
if __name__ == "__main__":
    main()
            
                