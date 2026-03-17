#!/usr/bin/env python3
"""
Celery Task Test Script
Verifies that Celery workers can receive and process tasks.
Tests both the broker (GCP Pub/Sub or Redis) and backend (Upstash Redis).
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
    print(f"\n{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}\n")


def print_success(text):
    print(f"✅ {text}")


def print_error(text):
    print(f"❌ {text}")


def print_info(text):
    print(f"ℹ️  {text}")


def test_celery_configuration():
    """Display current Celery configuration."""
    print_header("Celery Configuration")

    broker = celery_app.conf.broker_url
    backend = celery_app.conf.result_backend

    # Mask sensitive data
    if "@" in broker:
        broker_display = broker.split("@")[-1]
    else:
        broker_display = broker

    if "@" in backend:
        backend_display = backend.split("@")[-1]
    else:
        backend_display = backend

    print_info(f"Broker:  {broker_display}")
    print_info(f"Backend: {backend_display}")

    if "gcpubsub" in broker:
        print_info("Using GCP Pub/Sub as message broker")
    elif "redis" in broker:
        print_info("Using Redis as message broker")
    else:
        print_info(f"Using {broker.split('://')[0]} as message broker")


def test_worker_ping():
    """Check if any workers are online."""
    print_header("1. Worker Availability Check")

    print_info("Pinging workers (10s timeout)...")

    try:
        # Inspect active workers
        inspect = celery_app.control.inspect(timeout=10)
        active_workers = inspect.ping()

        if active_workers:
            print_success(f"Found {len(active_workers)} active worker(s):")
            for worker_name in active_workers.keys():
                print(f"    - {worker_name}")
            return True
        else:
            print_error("No workers responded")
            print_info(
                "Start workers with: celery -A app.worker.celery_app worker --loglevel=info"
            )
            return False

    except Exception as e:
        print_error(f"Failed to ping workers: {e}")
        return False


def test_send_task():
    """Send a test task and wait for result."""
    print_header("2. Send Test Task")

    # Check if qdrant_heartbeat task is available
    print_info("Sending 'qdrant_heartbeat' task to queue...")

    try:
        # Send the task
        result = celery_app.send_task("app.tasks.qdrant_heartbeat", queue="default")

        print_success(f"Task sent successfully - ID: {result.id}")
        print_info(f"Task state: {result.state}")

        return result

    except Exception as e:
        print_error(f"Failed to send task: {e}")
        return None


def test_wait_for_result(result, timeout=30):
    """Wait for task result."""
    print_header("3. Wait for Task Result")

    print_info(f"Waiting up to {timeout} seconds for task to complete...")

    start_time = time.time()
    last_state = None

    try:
        while time.time() - start_time < timeout:
            current_state = result.state

            if current_state != last_state:
                print_info(f"Task state: {current_state}")
                last_state = current_state

            if result.ready():
                elapsed = time.time() - start_time
                print_success(f"Task completed in {elapsed:.2f}s")

                if result.successful():
                    task_result = result.get(timeout=5)
                    print_success(f"Task result: {task_result}")
                    return True
                else:
                    print_error(f"Task failed: {result.info}")
                    return False

            time.sleep(0.5)

        print_error(f"Task did not complete within {timeout} seconds")
        print_info(f"Final state: {result.state}")
        return False

    except Exception as e:
        print_error(f"Error waiting for result: {e}")
        return False


def test_queue_stats():
    """Get queue statistics."""
    print_header("4. Queue Statistics")

    try:
        inspect = celery_app.control.inspect(timeout=10)

        # Get active tasks
        active = inspect.active()
        if active:
            total_active = sum(len(tasks) for tasks in active.values())
            print_info(f"Active tasks: {total_active}")
        else:
            print_info("Active tasks: 0")

        # Get reserved tasks
        reserved = inspect.reserved()
        if reserved:
            total_reserved = sum(len(tasks) for tasks in reserved.values())
            print_info(f"Reserved tasks: {total_reserved}")
        else:
            print_info("Reserved tasks: 0")

        # Get scheduled tasks
        scheduled = inspect.scheduled()
        if scheduled:
            total_scheduled = sum(len(tasks) for tasks in scheduled.values())
            print_info(f"Scheduled tasks: {total_scheduled}")
        else:
            print_info("Scheduled tasks: 0")

        return True

    except Exception as e:
        print_error(f"Failed to get queue stats: {e}")
        return False


def main():
    print("\n" + "=" * 70)
    print("  CELERY TASK TEST")
    print("  Verify workers can receive and process tasks")
    print("=" * 70)

    # Show configuration
    test_celery_configuration()

    # Test 1: Check if workers are online
    workers_online = test_worker_ping()
    if not workers_online:
        print("\n❌ TEST FAILED: No workers available")
        print("\nTo start workers, run:")
        print(
            "  venv/bin/celery -A app.worker.celery_app worker -Q default --loglevel=info"
        )
        return 1

    # Test 2: Send a task
    result = test_send_task()
    if not result:
        print("\n❌ TEST FAILED: Could not send task")
        return 1

    # Test 3: Wait for result
    task_completed = test_wait_for_result(result)
    if not task_completed:
        print("\n⚠️  TEST WARNING: Task did not complete or failed")
        print("   Check worker logs for details")

    # Test 4: Queue stats
    test_queue_stats()

    # Final summary
    print_header("SUMMARY")

    if task_completed:
        print_success("All tests passed!")
        print_info("Celery workers can receive and process tasks successfully")

        # Show what broker is being used
        broker = celery_app.conf.broker_url
        if "gcpubsub" in broker:
            print_success("GCP Pub/Sub integration is working correctly")
        elif "redis" in broker:
            print_success("Redis broker integration is working correctly")

        return 0
    else:
        print_error("Some tests failed")
        print_info("Check that:")
        print_info("  1. Workers are running")
        print_info("  2. Broker (GCP Pub/Sub or Redis) is accessible")
        print_info("  3. Backend (Upstash Redis) is accessible")
        print_info("  4. All environment variables are set correctly")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
