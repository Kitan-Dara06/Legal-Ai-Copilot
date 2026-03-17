#!/usr/bin/env python3
"""
GCP Pub/Sub Subscription Debugger
Diagnoses why Celery tasks aren't being consumed from Pub/Sub queues.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_header(text):
    print(f"\n{'=' * 80}")
    print(f"  {text}")
    print(f"{'=' * 80}\n")


def print_success(text):
    print(f"✅ {text}")


def print_error(text):
    print(f"❌ {text}")


def print_warning(text):
    print(f"⚠️  {text}")


def print_info(text):
    print(f"ℹ️  {text}")


def get_project_id():
    """Get GCP project ID from environment."""
    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print_error("GCP_PROJECT_ID not set in environment")
        sys.exit(1)
    return project_id


def list_topics(publisher, project_id):
    """List all topics in the project."""
    print_header("1. Topics in Project")

    try:
        project_path = f"projects/{project_id}"
        topics = list(
            publisher.list_topics(request={"project": project_path}, timeout=10.0)
        )

        if topics:
            print_info(f"Found {len(topics)} topic(s):")
            celery_topics = []
            for topic in topics:
                topic_name = topic.name.split("/")[-1]
                print(f"    - {topic_name}")
                if "celery" in topic_name.lower():
                    celery_topics.append(topic_name)

            if celery_topics:
                print_success(f"Found {len(celery_topics)} Celery-related topic(s)")
            else:
                print_warning("No topics with 'celery' in the name found")

            return topics, celery_topics
        else:
            print_warning("No topics found in project")
            print_info("Celery should auto-create topics when workers start")
            return [], []

    except Exception as e:
        print_error(f"Failed to list topics: {e}")
        return None, None


def list_subscriptions(subscriber, project_id):
    """List all subscriptions in the project."""
    print_header("2. Subscriptions in Project")

    try:
        project_path = f"projects/{project_id}"
        subscriptions = list(
            subscriber.list_subscriptions(
                request={"project": project_path}, timeout=10.0
            )
        )

        if subscriptions:
            print_info(f"Found {len(subscriptions)} subscription(s):")
            celery_subs = []
            for sub in subscriptions:
                sub_name = sub.name.split("/")[-1]
                topic_name = sub.topic.split("/")[-1] if sub.topic else "N/A"
                print(f"    - {sub_name}")
                print(f"      Topic: {topic_name}")
                print(f"      Ack deadline: {sub.ack_deadline_seconds}s")

                if "celery" in sub_name.lower():
                    celery_subs.append(sub)

            if celery_subs:
                print_success(
                    f"Found {len(celery_subs)} Celery-related subscription(s)"
                )
            else:
                print_warning("No subscriptions with 'celery' in the name found")
                print_info("Celery workers should auto-create subscriptions on startup")

            return subscriptions, celery_subs
        else:
            print_warning("No subscriptions found in project")
            print_error("This is the problem! Workers need subscriptions to pull tasks")
            print_info("Expected subscriptions:")
            print_info("  - celery@<hostname>.celery.pidbox")
            print_info("  - celery@<hostname>.default")
            print_info("  - celery@<hostname>.ocr")
            return [], []

    except Exception as e:
        print_error(f"Failed to list subscriptions: {e}")
        return None, None


def check_subscription_backlog(subscriber, subscriptions):
    """Check for pending messages in subscriptions."""
    print_header("3. Subscription Backlogs (Pending Messages)")

    if not subscriptions:
        print_warning("No subscriptions to check")
        return

    for sub in subscriptions:
        sub_name = sub.name.split("/")[-1]

        try:
            # Try to peek at messages without consuming them
            response = subscriber.pull(
                request={
                    "subscription": sub.name,
                    "max_messages": 1,
                    "return_immediately": True,
                },
                timeout=5.0,
            )

            if response.received_messages:
                print_warning(f"{sub_name}: Has pending messages!")
                msg = response.received_messages[0]
                print_info(f"  Message ID: {msg.message.message_id}")
                print_info(f"  Publish time: {msg.message.publish_time}")
                print_info(f"  Data preview: {msg.message.data[:100]}")

                # Don't ack - leave it for the worker

            else:
                print_info(f"{sub_name}: No pending messages")

        except Exception as e:
            print_error(f"{sub_name}: Failed to check - {e}")


def check_celery_queues():
    """Check what queues Celery is configured for."""
    print_header("4. Celery Queue Configuration")

    try:
        from app.worker import celery_app

        queues = celery_app.conf.task_queues
        if queues:
            print_info("Configured queues:")
            for queue_name, queue_config in queues.items():
                print(f"    - {queue_name}")
                if isinstance(queue_config, dict):
                    print(f"      Exchange: {queue_config.get('exchange', 'N/A')}")
                    print(
                        f"      Routing key: {queue_config.get('routing_key', 'N/A')}"
                    )
        else:
            print_warning("No explicit queues configured (using default)")

        default_queue = celery_app.conf.task_default_queue
        print_info(f"Default queue: {default_queue}")

        return True

    except Exception as e:
        print_error(f"Failed to load Celery config: {e}")
        return False


def check_worker_queues():
    """Check what queues active workers are consuming from."""
    print_header("5. Active Worker Queue Consumption")

    try:
        from app.worker import celery_app

        inspect = celery_app.control.inspect(timeout=10)
        active_queues = inspect.active_queues()

        if active_queues:
            for worker_name, queues in active_queues.items():
                print_info(f"{worker_name}:")
                for queue_info in queues:
                    queue_name = queue_info.get("name", "unknown")
                    print(f"    - {queue_name}")
                    print(
                        f"      Exchange: {queue_info.get('exchange', {}).get('name', 'N/A')}"
                    )
                    print(f"      Routing key: {queue_info.get('routing_key', 'N/A')}")
        else:
            print_warning("Could not get active queue information from workers")

        return True

    except Exception as e:
        print_error(f"Failed to inspect workers: {e}")
        return False


def check_iam_permissions(project_id):
    """Check IAM permissions for Pub/Sub."""
    print_header("6. IAM Permissions Check")

    try:
        from google.auth import default

        credentials, project = default()

        print_info(
            f"Authenticated as: {getattr(credentials, 'service_account_email', 'User account')}"
        )
        print_info("Required permissions:")
        print_info("  ✓ pubsub.topics.create")
        print_info("  ✓ pubsub.topics.publish")
        print_info("  ✓ pubsub.subscriptions.create")
        print_info("  ✓ pubsub.subscriptions.consume")
        print_info("  ✓ pubsub.subscriptions.delete")

        print_warning("Cannot automatically verify permissions")
        print_info("If tasks aren't working, check IAM roles include:")
        print_info("  - roles/pubsub.publisher")
        print_info("  - roles/pubsub.subscriber")

    except Exception as e:
        print_error(f"Could not check credentials: {e}")


def recommend_fixes(celery_topics, celery_subs):
    """Provide recommendations based on findings."""
    print_header("RECOMMENDATIONS")

    if not celery_topics:
        print_error("CRITICAL: No Celery topics found")
        print_info("Action: Restart Celery workers to auto-create topics")
        print_info(
            "Command: celery -A app.worker.celery_app worker -Q default --loglevel=info"
        )

    if not celery_subs:
        print_error("CRITICAL: No Celery subscriptions found")
        print_info("This is why tasks stay PENDING!")
        print_info("\nPossible causes:")
        print_info("  1. Workers started but crashed before creating subscriptions")
        print_info("  2. Insufficient IAM permissions")
        print_info("  3. Kombu-gcpubsub transport not properly configured")
        print_info("\nTry:")
        print_info("  1. Kill all workers: pkill -9 -f 'celery.*worker'")
        print_info("  2. Clear any stale state")
        print_info("  3. Restart workers with verbose logging:")
        print_info(
            "     celery -A app.worker.celery_app worker -Q default --loglevel=debug"
        )
        print_info("  4. Watch for subscription creation in logs")

    if celery_topics and not celery_subs:
        print_warning("Topics exist but subscriptions don't")
        print_info("Workers may have crashed during subscription creation")
        print_info("Check worker logs for errors")

    if celery_topics and celery_subs:
        print_success("Both topics and subscriptions exist!")
        print_info("If tasks are still PENDING, check:")
        print_info("  1. Worker logs for pull/ack errors")
        print_info("  2. Task is being sent to the correct queue")
        print_info("  3. Workers are listening on the correct queues")


def main():
    print("\n" + "=" * 80)
    print("  GCP PUB/SUB SUBSCRIPTION DEBUGGER")
    print("  Diagnose Celery task consumption issues")
    print("=" * 80)

    # Get project ID
    project_id = get_project_id()
    print_info(f"Project: {project_id}\n")

    # Initialize clients
    try:
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()
    except ImportError:
        print_error("google-cloud-pubsub not installed")
        print_info("Run: pip install google-cloud-pubsub")
        return 1
    except Exception as e:
        print_error(f"Failed to create Pub/Sub clients: {e}")
        return 1

    # Run diagnostics
    topics, celery_topics = list_topics(publisher, project_id)
    if topics is None:
        return 1

    subscriptions, celery_subs = list_subscriptions(subscriber, project_id)
    if subscriptions is None:
        return 1

    check_subscription_backlog(subscriber, subscriptions)
    check_celery_queues()
    check_worker_queues()
    check_iam_permissions(project_id)

    # Provide recommendations
    recommend_fixes(celery_topics, celery_subs)

    # Summary
    print_header("SUMMARY")
    if celery_topics and celery_subs:
        print_success("Pub/Sub infrastructure looks good")
        print_info(
            "If tasks are still stuck, the issue is likely in worker consumption"
        )
    else:
        print_error("Pub/Sub infrastructure incomplete")
        print_info("Follow recommendations above to fix")

    return 0


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
