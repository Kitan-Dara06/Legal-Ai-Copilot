#!/usr/bin/env python3
"""
Manually trigger a Celery task to verify workers are receiving tasks from Pub/Sub.
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import celery_app


def print_header(text):
    print(f"\n{'=' * 80}")
    print(f"  {text}")
    print(f"{'=' * 80}\n")


def print_success(text):
    print(f"✅ {text}")


def print_error(text):
    print(f"❌ {text}")


def print_info(text):
    print(f"ℹ️  {text}")


def main():
    print("\n" + "=" * 80)
    print("  CELERY TASK TRIGGER TEST")
    print("  Send a task to verify workers are consuming from Pub/Sub")
    print("=" * 80)

    print_header("Celery Configuration")
    broker = celery_app.conf.broker_url
    if "gcpubsub" in broker:
        print_info("Broker: GCP Pub/Sub")
    elif "redis" in broker:
        print_info("Broker: Redis")
    else:
        print_info(f"Broker: {broker.split('://')[0]}")

    print_header("1. Sending qdrant_heartbeat task")

    try:
        # Send the heartbeat task to the default queue
        result = celery_app.send_task("app.tasks.qdrant_heartbeat", queue="default")

        print_success(f"Task sent! Task ID: {result.id}")
        print_info(f"Initial state: {result.state}")

    except Exception as e:
        print_error(f"Failed to send task: {e}")
        return 1

    print_header("2. Waiting for result (30s timeout)")

    start_time = time.time()
    last_state = None

    try:
        while time.time() - start_time < 30:
            current_state = result.state

            if current_state != last_state:
                print_info(f"Task state: {current_state}")
                last_state = current_state

            if result.ready():
                elapsed = time.time() - start_time
                print_success(f"Task completed in {elapsed:.2f}s")

                if result.successful():
                    task_result = result.get(timeout=5)
                    print_success(f"Result: {task_result}")

                    print_header("SUMMARY")
                    print_success("Workers are receiving and processing tasks!")
                    print_info("GCP Pub/Sub integration is working correctly")
                    return 0
                else:
                    print_error(f"Task failed: {result.info}")
                    return 1

            time.sleep(0.5)

        print_error(f"Task did not complete within 30 seconds")
        print_info(f"Final state: {result.state}")
        print_info("\nPossible issues:")
        print_info("  1. No workers listening to 'default' queue")
        print_info("  2. Workers can't pull from GCP Pub/Sub")
        print_info("  3. Task is stuck in the queue")
        print_info("\nCheck worker logs for details:")
        print_info("  tail -f logs/Celery_pdf.log | grep -E 'received|Task|qdrant'")
        return 1

    except Exception as e:
        print_error(f"Error waiting for result: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
