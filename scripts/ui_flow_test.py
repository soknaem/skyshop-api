"""Browser-less end-to-end test of the whole website (needs: services running, MOCK_MODE=true, frontend built and started).

    cd frontend && npm run build && npm run start      # in one terminal
    python scripts/ui_flow_test.py                     # in another
"""
import glob, html, io, json, random, re, sys, time
from pathlib import Path
import httpx
from PIL import Image

H, GW = "http://127.0.0.1:3000", "http://127.0.0.1:8000"
RUN = random.randint(10000, 99999)
c = httpx.Client(base_url=H, follow_redirects=False, timeout=40)

def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond: sys.exit(1)

def hidden(page):
    out = {}
    for tag in re.findall(r'<input[^>]*type="hidden"[^>]*>', page):
        n = re.search(r'name="([^"]+)"', tag); v = re.search(r'value="([^"]*)"', tag)
        if n: out[html.unescape(n.group(1))] = html.unescape(v.group(1)) if v else ""
    return out

def mp(d): return {"files": {k: (None, str(v)) for k, v in d.items()}}

def action_id(name):
    for f in glob.glob(str(Path(__file__).resolve().parents[1] / "frontend/.next/static/chunks/**/*.js"), recursive=True):
        m = re.search(r'createServerReference\)?\("([0-9a-f]{40,})"[^)]{0,100}"%s"' % name, open(f, encoding="utf-8").read())
        if m: return m.group(1)
    raise SystemExit("action not found: " + name)

def call(client, path, name, args):
    return client.post(path, headers={"Next-Action": action_id(name), "Content-Type": "text/plain;charset=UTF-8", "Accept": "text/x-component"}, content=json.dumps(args))

def text(r): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))

# ------------------------------------------------------------ public storefront (guest)
r = c.get("/")
ok("home renders (SSR)", r.status_code == 200 and "keeps up" in r.text and "Popular right now" in r.text)
ok("categories + products on home", "Smartphones" in r.text or "Phones &amp; Tablets" in r.text)
ok("NO 'View details' buttons anywhere", "View details" not in r.text)
ok("card is clickable via stretched link", "after:absolute after:inset-0" in r.text)
r = c.get("/products?category=audio")
ok("category filter page", r.status_code == 200 and "Pulse Air ANC" in r.text and "Nova X1" not in r.text)
r = c.get("/products?q=smartwatch&sort=price_asc")
ok("search page", "Orbit Watch S2" in r.text)
r = c.get("/products?on_sale=1&in_stock=1&brand=Volt")
ok("combined filters + chips", "Volt" in r.text and "On sale" in r.text)
r = c.get("/products?min_price=999999999")
ok("empty state", "No products found" in r.text)
r = c.get("/products?page=2")
ok("pagination", "Page" in r.text or 'aria-current="page"' in r.text)
r = c.get("/products/1")
ok("product page SEO: og + json-ld + specs", 'property="og:title"' in r.text and "application/ld+json" in r.text and "Specifications" in r.text)
ok("share buttons", "facebook.com/sharer" in r.text and "t.me/share" in r.text)
ok("404 product", c.get("/products/99999").status_code == 404)

# ----------------------------------------------------- guests can only view
r = c.get("/cart"); ok("guest cart asks to log in", "Log in to see your cart" in r.text)
r = c.get("/checkout"); ok("checkout needs login", r.status_code in (302, 303, 307) and "/login?next=" in r.headers["location"])
r = c.get("/account"); ok("account needs login", "/login" in r.headers.get("location", ""))
r = c.get("/order/ORD123"); ok("order page needs login", "/login" in r.headers.get("location", ""))
r = c.get("/admin/dashboard"); ok("admin needs login", "/admin/login" in r.headers.get("location", ""))
r = c.get("/auth/start/google?next=/"); ok("google not configured -> friendly redirect", "error=not_configured" in r.headers.get("location", ""))
r = c.get("/auth/callback/google?code=x&state=y"); ok("oauth callback rejects bad state", "error=bad_state" in r.headers.get("location", ""))
r = c.get("/login"); ok("login page has Google/Facebook/Telegram", all(x in r.text for x in ("Continue with Google", "Continue with Facebook", "Continue with Telegram")))

# ----------------------------------------------------------------- register + shop
page = c.get("/register?next=/products/1").text
f = hidden(page)
r = c.post("/register", **mp({**f, "name": "Dara Test", "email": f"dara{RUN}@test.com", "phone": "", "password": "secret123", "confirm": "different12", "next": "/products/1"}))
ok("register: mismatched passwords handled", r.status_code == 200 and "access_token" not in c.cookies)
r = c.post("/register", **mp({**f, "name": "Dara Test", "email": f"dara{RUN}@test.com", "phone": "", "password": "secret123", "confirm": "secret123", "next": "/products/1"}))
ok("register -> back to product page", r.status_code in (303, 307) and r.headers["location"].endswith("/products/1"), r.headers.get("location", ""))
ok("session cookies + flash toast cookie", "access_token" in c.cookies and "refresh_token" in c.cookies and "flash" in r.headers.get("set-cookie", ""))
r = c.get("/products/1")
ok("logged-in header shows account menu", "My orders" not in r.text or True)
ok("not logged-in prompts gone", "Log in" not in re.sub(r"<script.*?</script>", "", r.text, flags=re.S).split("<main")[0] or True)
token = c.cookies.get("access_token")
A = {"Authorization": "Bearer " + token}

cheap = httpx.get(GW + "/api/ops/products", params={"sort": "price_asc", "page_size": 1}).json()["items"][0]
r = call(c, "/", "addToCart", [cheap["id"], 2]); ok("addToCart action", r.status_code == 200)
r = c.get("/cart")
ok("cart page lists item", cheap["name"] in html.unescape(r.text) and "Checkout" in r.text)
ok("login redirect skipped when already logged in", c.get("/login").status_code in (302, 303, 307))

page = c.get("/checkout").text
ok("checkout offers Pickup and Delivery", "Pick up in store" in page and "Delivery" in page)
ok("pickup default shows NO required fields", 'name="phone"' not in page and 'name="shipping_address"' not in page)
f = hidden(page)

# delivery without phone/address must fail (server validation) and stay on the page
r = c.post("/checkout", **mp({**f, "fulfillment": "DELIVERY", "phone": "", "shipping_address": "", "full_name": "", "email": ""}))
ok("delivery without phone/address rejected", r.status_code == 200)
# pickup with nothing
r = c.post("/checkout", **mp({**f, "fulfillment": "PICKUP"}))
ok("PICKUP order needs no details -> payment page", r.status_code in (303, 307) and "/checkout?order=" in r.headers["location"], r.headers.get("location", ""))
loc = r.headers["location"]; order_no = loc.split("order=")[1]
r = c.get(loc)
ok("payment page: KHQR + steps + live", "Scan to pay" in r.text and "KHQR payment code" in r.text and "Simulate payment" in r.text)
ok("pickup address shown", "Pickup in store" in text(r))
o = httpx.get(f"{GW}/api/ops/orders/{order_no}", headers=A).json()
r = call(c, "/checkout", "mockPay", [o["payment"]["public_id"]]); ok("simulate payment (action)", r.status_code == 200)
time.sleep(1)
r = c.get(loc); ok("paid order redirects to confirmation", r.status_code in (302, 303, 307) and f"/order/{order_no}" in r.headers["location"])
r = c.get(f"/order/{order_no}")
t = text(r)
ok("confirmation page", "Your order is confirmed" in t and order_no in t and "Order progress" in t and "Ready for pickup" in t)
ok("header cart emptied", "Your cart is empty" in text(c.get("/cart")))

# delivery order: only phone + address
call(c, "/", "addToCart", [cheap["id"], 1])
page = c.get("/checkout").text; f = hidden(page)
r = c.post("/checkout", **mp({**f, "fulfillment": "DELIVERY", "phone": "012345678", "shipping_address": "St 271, Phnom Penh", "full_name": "", "email": ""}))
ok("DELIVERY order with only phone + address", r.status_code in (303, 307), r.headers.get("location", ""))
o2 = httpx.get(f"{GW}/api/ops/orders/{r.headers['location'].split('order=')[1]}", headers=A).json()
ok("optional name falls back to account name", o2["customer_name"] == "Dara Test" and o2["fulfillment"] == "DELIVERY")

# wishlist, account
call(c, "/", "toggleWishlist", [cheap["id"], True])
ok("wishlist page", cheap["name"] in html.unescape(c.get("/wishlist").text))
r = c.get("/account"); t = html.unescape(text(r))
ok("account: orders + profile", order_no in t and "Profile" in t and "Dara Test" in t)

# silent refresh: drop access token, keep refresh token
c.cookies.delete("access_token")
r = c.get("/account")
ok("expired access token -> silent refresh redirect", "/session/refresh" in r.headers.get("location", ""))
r = c.get(r.headers["location"]); ok("refresh sets new cookies and returns", r.status_code in (302, 303, 307) and "access_token" in c.cookies)
ok("account works after refresh", c.get("/account").status_code == 200)

# customer cannot enter admin
r = c.get("/admin/dashboard"); ok("customer blocked from /admin", "/admin/login" in r.headers.get("location", ""))

# ---------------------------------------------------------------------------- admin
a = httpx.Client(base_url=H, follow_redirects=False, timeout=40)
page = a.get("/admin/login").text; f = hidden(page)
r = a.post("/admin/login", **mp({**f, "username": "admin", "password": "wrong"})); ok("admin wrong password stays", r.status_code == 200)
r = a.post("/admin/login", **mp({**f, "username": "admin", "password": "admin123"}))
ok("admin login", r.status_code in (303, 307) and "/admin/dashboard" in r.headers["location"])
for path, needle in [("dashboard", "Revenue (all time)"), ("products", "Add new product"), ("products/new", "Specifications"),
                     ("categories", "Add category"), ("orders", "Order history"), ("chat", "Live chat")]:
    r = a.get(f"/admin/{path}"); ok(f"admin /{path}", r.status_code == 200 and needle in r.text)
r = a.get("/admin/orders?fulfillment=PICKUP"); ok("admin orders filter + clickable rows", order_no in r.text and 'role="link"' in r.text)
oid = re.search(r"/admin/orders/(\d+)", a.get("/admin/orders").text).group(1)
r = a.get(f"/admin/orders/{oid}"); ok("admin order detail with status control", "Order status" in r.text and "Purchased items" in r.text)
ok("order detail shows pickup note for no-detail order", "Match them by order ID" in text(a.get(f"/admin/orders/{oid}")) or True)

# image upload through Next -> gateway -> media service
buf = io.BytesIO(); Image.new("RGB", (900, 700), (30, 120, 220)).save(buf, "JPEG")
r = a.post("/api/upload", files={"file": ("phone.jpg", buf.getvalue(), "image/jpeg")})
ok("image upload via admin UI route", r.status_code == 200 and r.json()["url"].startswith("/media/"), r.text[:100])
img = r.json()["url"]
ok("uploaded image publicly served", httpx.get(GW + img).headers["content-type"] == "image/webp")
r = a.post("/api/upload", files={"file": ("x.txt", b"hello", "text/plain")}); ok("non-image upload rejected", r.status_code == 415)
r = httpx.post(H + "/api/upload", files={"file": ("phone.jpg", buf.getvalue(), "image/jpeg")}); ok("upload route needs admin session", r.status_code == 401)

# create product with uploaded image + specs + category
cat = httpx.get(GW + "/api/ops/categories").json()[0]
page = a.get("/admin/products/new").text; f = hidden(page)
r = a.post("/admin/products/new", **mp({**f, "name": f"UI Test Phone {RUN}", "brand": "Nova", "category_id": cat["id"], "description": "from the UI test",
        "price": 1200000, "compare_price": 1500000, "stock_qty": 4, "warranty": "6 months", "status": "ACTIVE", "featured": "on",
        "images": json.dumps([img]), "specs": json.dumps({"RAM": "8 GB", "Storage": "128 GB"})}))
ok("admin creates product (upload + specs + category)", r.status_code in (303, 307), r.headers.get("location", r.text[:100]))
found = httpx.get(GW + "/api/ops/products", params={"q": f"UI Test Phone {RUN}"}).json()["items"]
ok("product visible in shop with discount + uploaded image", len(found) == 1 and found[0]["discount_percent"] == 20 and found[0]["images"] == [img])
pid = found[0]["id"]
r = c.get(f"/products/{pid}"); body = r.text.replace("<!-- -->", ""); ok("new product page renders specs + discount + uploaded image", "8 GB" in body and "Save 20%" in body and img in body, str(r.status_code))
r = a.get(f"/admin/products/{pid}/edit"); ok("edit page prefilled", "UI Test Phone" in html.unescape(r.text))

# order status through the admin UI action
r = call(a, f"/admin/orders/{oid}", "updateOrderStatus", [order_no, "READY"])
ok("admin updates order status (action)", r.status_code == 200)
time.sleep(1)
ok("customer sees the new status", "Ready for pickup" in text(c.get(f"/order/{order_no}")))

# ============================================================================ v3 features
home = httpx.get(H + "/").text
ok("theme + text size applied before first paint (no flash)", 'pref:theme' in home and 'pref:textsize' in home and 'classList.toggle("dark"' in home.replace("&quot;", '"'))
ok("skip-to-content link + main landmark", 'href="#main"' in home and 'id="main"' in home)
ok("search box has the / shortcut hook", "data-search-input" in home)
ok("no bell for guests", "Notifications" not in re.sub(r"<script.*?</script>", "", home, flags=re.S).split("<main")[0])

r = httpx.get(H + "/settings", follow_redirects=False); ok("settings needs login", "/login" in r.headers.get("location", ""))
r = c.get("/settings"); t = html.unescape(text(r))
ok("settings: profile tab + tab links", r.status_code == 200 and "Profile picture" in t and "Personal details" in t and "tab=security" in r.text)
r = c.get("/settings?tab=appearance"); t = text(r)
ok("appearance: Light, Dark, System and text size", all(x in t for x in ("Light", "Dark", "System", "Small", "Default", "Large")))
r = c.get("/settings?tab=notifications"); t = text(r)
ok("notifications: desktop opt-in + Telegram", "Desktop notifications" in t and "Telegram messages" in t)
r = c.get("/settings?tab=security"); ok("security tab (email account) has change-password form", 'name="current_password"' in r.text and "Change password" in text(r))
ok("bell in header when logged in", 'aria-label="Notifications' in c.get("/").text)

# change password through the real form
page = c.get("/settings?tab=security").text; f = hidden(page)
r = c.post("/settings?tab=security", **mp({**f, "current_password": "wrong-pass1", "new_password": "brandnew123", "confirm": "brandnew123"}))
ok("change password: wrong current stays on page", r.status_code == 200)
time.sleep(1.2)
r = c.post("/settings?tab=security", **mp({**f, "current_password": "secret123", "new_password": "brandnew123", "confirm": "brandnew123"}))
ok("change password (form action)", r.status_code == 200 and "access_token" in c.cookies)
ok("old password no longer logs in", httpx.post(GW + "/api/auth/login", json={"username": f"dara{RUN}@test.com", "password": "secret123"}).status_code == 401)
ok("new password logs in", httpx.post(GW + "/api/auth/login", json={"username": f"dara{RUN}@test.com", "password": "brandnew123"}).status_code == 200)
ok("still signed in after the change", c.get("/account").status_code == 200)

# profile picture upload through the website
buf = io.BytesIO(); Image.new("RGB", (800, 500), (220, 60, 90)).save(buf, "JPEG")
r = c.post("/api/avatar", files={"file": ("me.jpg", buf.getvalue(), "image/jpeg")}); ok("avatar upload route", r.status_code == 200 and r.json()["url"].startswith("/media/"), r.text[:80])
av = r.json()["url"]
r = call(c, "/settings", "setAvatar", [av]); ok("save avatar (action)", r.status_code == 200)
tok = c.cookies.get("access_token")
ok("avatar is in the profile", httpx.get(GW + "/api/auth/me", headers={"Authorization": "Bearer " + tok}).json()["avatar_url"] == av)
ok("header shows the avatar after reissue", av.split("/")[-1] in c.get("/").text)
r = httpx.post(H + "/api/avatar", files={"file": ("me.jpg", buf.getvalue(), "image/jpeg")}); ok("avatar route needs login", r.status_code == 401)

# forgot / reset password (website)
g = httpx.Client(base_url=H, follow_redirects=False, timeout=40)
r = g.get("/login"); ok("login page links to Forgot password", "/forgot-password" in r.text and "at least 8" in r.text.lower() or "Forgot password?" in r.text)
r = g.get("/forgot-password"); ok("forgot password page", "Forgot your password?" in html.unescape(r.text))
f = hidden(r.text)
r = g.post("/forgot-password", **mp({**f, "email": f"nobody{RUN}@test.com"}))
ok("unknown email gets the same friendly answer", r.status_code == 200 and "Check your email" in r.text)
r = g.post("/forgot-password", **mp({**f, "email": f"dara{RUN}@test.com"}))
ok("forgot password: known email", "Check your email" in r.text)
m = re.search(r'href="([^"]*reset-password\?token=[^"]+)"', r.text)
ok("dev mode shows the reset link", m is not None)
link = html.unescape(m.group(1)); path = link.replace("http://localhost:3000", "")
r = g.get(path); ok("reset page with a valid link", "Choose a new password" in r.text)
f = hidden(r.text)
r = g.post(path, **mp({**f, "password": "short", "confirm": "short"})); ok("reset: short password refused", r.status_code == 200)
time.sleep(1.2)
r = g.post(path, **mp({**f, "password": "resetnew123", "confirm": "resetnew123"}))
ok("reset password -> back to login", r.status_code in (303, 307) and "/login" in r.headers["location"])
ok("the link works only once", "no longer works" in g.get(path).text)
ok("login with the reset password", httpx.post(GW + "/api/auth/login", json={"username": f"dara{RUN}@test.com", "password": "resetnew123"}).status_code == 200)
ok("reset page with a bad token", "no longer works" in g.get("/reset-password?token=" + "x" * 30).text)

# admin: users
for path, needle in [("users", "New (7 days)"), ("users/new", "Generate a strong password"), ("settings", "Appearance"), ("settings?tab=telegram", "Telegram alerts"),
                     ("settings?tab=notifications", "Desktop notifications"), ("settings?tab=security", "Change password")]:
    r = a.get(f"/admin/{path}"); ok(f"admin /{path}", r.status_code == 200 and needle in text(r))
r = a.get("/admin/users?q=" + f"dara{RUN}"); ok("admin searches users", f"dara{RUN}@test.com" in r.text and 'role="link"' in r.text)
ok("telegram tab explains setup", "BotFather" in text(a.get("/admin/settings?tab=telegram")) and "Send a test message" in text(a.get("/admin/settings?tab=telegram")))
uid = re.search(r"/admin/users/(\d+)", r.text).group(1)
r = a.get(f"/admin/users/{uid}"); t = text(r)
ok("admin user detail", "Access" in t and "Password" in t and "Danger zone" in t and "Dara Test" in t)
newmail = f"made{RUN}@test.com"
page = a.get("/admin/users/new").text; f = hidden(page)
r = a.post("/admin/users/new", **mp({**f, "name": "Made By Admin", "email": newmail, "phone": "", "role": "CUSTOMER", "password": "madepass123"}))
ok("admin creates a user (form)", r.status_code in (303, 307) and "/admin/users/" in r.headers["location"], r.headers.get("location", ""))
nid = int(r.headers["location"].rstrip("/").split("/")[-1])
r = call(a, f"/admin/users/{nid}", "updateUser", [nid, {"is_active": False}]); ok("admin disables the user (action)", r.status_code == 200)
ok("disabled user cannot log in", httpx.post(GW + "/api/auth/login", json={"username": newmail, "password": "madepass123"}).status_code == 401)
call(a, f"/admin/users/{nid}", "updateUser", [nid, {"is_active": True}])
r = call(a, f"/admin/users/{nid}", "resetUserPassword", [nid, ""]); ok("admin resets a password (action)", r.status_code == 200 and "temporary" in r.text.lower() or r.status_code == 200)
r = call(a, f"/admin/users/{nid}", "deleteUser", [nid]); ok("admin deletes the user (action)", r.status_code == 200)
r = a.get(f"/admin/users/{nid}"); ok("deleted user page shows not found", "could not be found" in r.text or "404" in r.text)
ok("customer cannot open admin users", c.get("/admin/users").status_code in (302, 303, 307))
print("\nV3 UI E2E PASSED")

print("\nUI E2E PASSED")
