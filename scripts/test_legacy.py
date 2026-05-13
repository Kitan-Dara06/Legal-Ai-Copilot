import redis

print("Attempting direct connection...")

try:
    # Notice we are passing every piece individually, no URL strings.
    r = redis.Redis(
        host="leading-skylark-45361.upstash.io",
        port=6379,
        password="", # Paste it exactly as copied
        ssl=True,
        ssl_cert_reqs="none"
    )
    
    r.ping()
    print("✅ SUCCESS! Upstash is connected. The URL parser was the problem.")

except Exception as e:
    print(f"❌ FAILED: {e}")