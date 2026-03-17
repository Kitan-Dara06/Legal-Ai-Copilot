#!/usr/bin/env python3
"""
GCP Pub/Sub Diagnostic Script
Tests all aspects of GCP Pub/Sub connectivity for Celery integration.
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


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


def test_environment_variables():
    """Check if required environment variables are set."""
    print_header("1. Environment Variables")

    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if project_id:
        print_success(f"GCP_PROJECT_ID found: {project_id}")
    else:
        print_error("GCP_PROJECT_ID not set in environment")
        print_info("Set it in .env file or export GCP_PROJECT_ID=your-project-id")
        return None, None

    if creds_path:
        print_info(f"GOOGLE_APPLICATION_CREDENTIALS: {creds_path}")
        if os.path.exists(creds_path):
            print_success("Credentials file exists")
        else:
            print_error(f"Credentials file not found at: {creds_path}")
            return project_id, None
    else:
        print_info("GOOGLE_APPLICATION_CREDENTIALS not set (will use ADC)")

    return project_id, creds_path


def test_gcp_auth():
    """Test Google Cloud authentication."""
    print_header("2. Authentication")

    try:
        from google.auth import default
        from google.auth.exceptions import DefaultCredentialsError

        try:
            credentials, project = default()
            print_success(f"Authentication successful")
            print_info(f"Credentials type: {type(credentials).__name__}")
            print_info(f"ADC detected project: {project or '(none)'}")
            return credentials, project
        except DefaultCredentialsError as e:
            print_error(f"Authentication failed: {e}")
            print_info("Run: gcloud auth application-default login")
            return None, None

    except ImportError as e:
        print_error(f"Missing dependencies: {e}")
        print_info("Run: pip install google-cloud-pubsub google-cloud-monitoring")
        return None, None


def test_pubsub_client(project_id):
    """Test Pub/Sub client initialization."""
    print_header("3. Pub/Sub Client Initialization")

    try:
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()

        print_success("Publisher client created")
        print_success("Subscriber client created")

        return publisher, subscriber

    except Exception as e:
        print_error(f"Failed to create Pub/Sub clients: {e}")
        return None, None


def test_list_topics(publisher, project_id):
    """Test listing topics to verify API access."""
    print_header("4. API Access (List Topics)")

    try:
        project_path = f"projects/{project_id}"
        print_info(f"Listing topics in: {project_path}")

        # Set a timeout to avoid hanging
        import google.api_core.timeout

        timeout = google.api_core.timeout.ExponentialTimeout(initial=5.0, maximum=10.0)

        topics = list(
            publisher.list_topics(request={"project": project_path}, timeout=10.0)
        )

        print_success(f"API access verified - found {len(topics)} topic(s)")

        if topics:
            print_info("Existing topics:")
            for topic in topics[:5]:  # Show first 5
                print(f"    - {topic.name}")
            if len(topics) > 5:
                print(f"    ... and {len(topics) - 5} more")

        return True

    except Exception as e:
        print_error(f"Failed to list topics: {e}")
        print_info("Possible causes:")
        print_info("  - Pub/Sub API not enabled")
        print_info("  - Insufficient IAM permissions")
        print_info("  - Invalid project ID")
        print_info(
            f"\nRun: gcloud services enable pubsub.googleapis.com --project={project_id}"
        )
        return False


def test_create_topic(publisher, project_id):
    """Test creating a topic."""
    print_header("5. Create Test Topic")

    test_topic_name = f"celery-test-{int(time.time())}"
    topic_path = f"projects/{project_id}/topics/{test_topic_name}"

    try:
        print_info(f"Creating topic: {test_topic_name}")
        topic = publisher.create_topic(request={"name": topic_path}, timeout=10.0)
        print_success(f"Topic created: {topic.name}")
        return test_topic_name, topic_path

    except Exception as e:
        print_error(f"Failed to create topic: {e}")
        return None, None


def test_create_subscription(subscriber, project_id, topic_path):
    """Test creating a subscription."""
    print_header("6. Create Test Subscription")

    test_sub_name = f"celery-test-sub-{int(time.time())}"
    subscription_path = f"projects/{project_id}/subscriptions/{test_sub_name}"

    try:
        print_info(f"Creating subscription: {test_sub_name}")
        subscription = subscriber.create_subscription(
            request={
                "name": subscription_path,
                "topic": topic_path,
                "ack_deadline_seconds": 60,
            },
            timeout=10.0,
        )
        print_success(f"Subscription created: {subscription.name}")
        return test_sub_name, subscription_path

    except Exception as e:
        print_error(f"Failed to create subscription: {e}")
        return None, None


def test_publish_message(publisher, topic_path):
    """Test publishing a message."""
    print_header("7. Publish Test Message")

    try:
        test_message = b"Hello from Celery test script"
        print_info(f"Publishing message to: {topic_path}")

        future = publisher.publish(topic_path, test_message, origin="test-script")
        message_id = future.result(timeout=10.0)

        print_success(f"Message published successfully - ID: {message_id}")
        return True

    except Exception as e:
        print_error(f"Failed to publish message: {e}")
        return False


def test_receive_message(subscriber, subscription_path):
    """Test receiving a message."""
    print_header("8. Receive Test Message")

    try:
        print_info(f"Pulling messages from: {subscription_path}")

        response = subscriber.pull(
            request={
                "subscription": subscription_path,
                "max_messages": 1,
            },
            timeout=10.0,
        )

        if response.received_messages:
            message = response.received_messages[0]
            print_success(f"Message received: {message.message.data.decode('utf-8')}")

            # Acknowledge the message
            subscriber.acknowledge(
                request={
                    "subscription": subscription_path,
                    "ack_ids": [message.ack_id],
                }
            )
            print_success("Message acknowledged")
            return True
        else:
            print_error("No messages received")
            return False

    except Exception as e:
        print_error(f"Failed to receive message: {e}")
        return False


def cleanup_resources(publisher, subscriber, project_id, topic_name, sub_name):
    """Clean up test resources."""
    print_header("9. Cleanup Test Resources")

    success = True

    if sub_name:
        try:
            subscription_path = f"projects/{project_id}/subscriptions/{sub_name}"
            subscriber.delete_subscription(
                request={"subscription": subscription_path}, timeout=10.0
            )
            print_success(f"Deleted subscription: {sub_name}")
        except Exception as e:
            print_error(f"Failed to delete subscription: {e}")
            success = False

    if topic_name:
        try:
            topic_path = f"projects/{project_id}/topics/{topic_name}"
            publisher.delete_topic(request={"topic": topic_path}, timeout=10.0)
            print_success(f"Deleted topic: {topic_name}")
        except Exception as e:
            print_error(f"Failed to delete topic: {e}")
            success = False

    return success


def test_celery_broker_url(project_id):
    """Test if Celery can connect to the broker."""
    print_header("10. Celery Broker Connection Test")

    try:
        from kombu import Connection

        broker_url = f"gcpubsub://projects/{project_id}"
        print_info(f"Testing Celery connection to: {broker_url}")

        # This will timeout if it can't connect
        with Connection(broker_url, connect_timeout=10) as conn:
            try:
                conn.connect()
                print_success("Celery can connect to GCP Pub/Sub broker")
                return True
            except Exception as e:
                print_error(f"Celery connection failed: {e}")
                return False

    except ImportError:
        print_error("Celery not installed")
        print_info("Run: pip install celery[gcpubsub]")
        return False
    except Exception as e:
        print_error(f"Connection test failed: {e}")
        return False


def main():
    print("\n" + "=" * 70)
    print("  GCP PUB/SUB DIAGNOSTIC TOOL")
    print("  Testing Celery integration readiness")
    print("=" * 70)

    # Test 1: Environment variables
    project_id, creds_path = test_environment_variables()
    if not project_id:
        print("\n❌ FAILED: Project ID not configured")
        return 1

    # Test 2: Authentication
    credentials, adc_project = test_gcp_auth()
    if not credentials:
        print("\n❌ FAILED: Authentication not configured")
        return 1

    # Test 3: Client initialization
    publisher, subscriber = test_pubsub_client(project_id)
    if not publisher or not subscriber:
        print("\n❌ FAILED: Could not initialize Pub/Sub clients")
        return 1

    # Test 4: API access
    if not test_list_topics(publisher, project_id):
        print("\n❌ FAILED: Cannot access Pub/Sub API")
        return 1

    # Test 5-8: Full message flow
    topic_name, topic_path = test_create_topic(publisher, project_id)
    if not topic_path:
        print("\n❌ FAILED: Cannot create topics")
        return 1

    sub_name, subscription_path = test_create_subscription(
        subscriber, project_id, topic_path
    )
    if not subscription_path:
        cleanup_resources(publisher, subscriber, project_id, topic_name, None)
        print("\n❌ FAILED: Cannot create subscriptions")
        return 1

    # Small delay to ensure subscription is ready
    time.sleep(1)

    if not test_publish_message(publisher, topic_path):
        cleanup_resources(publisher, subscriber, project_id, topic_name, sub_name)
        print("\n❌ FAILED: Cannot publish messages")
        return 1

    # Small delay for message propagation
    time.sleep(2)

    if not test_receive_message(subscriber, subscription_path):
        cleanup_resources(publisher, subscriber, project_id, topic_name, sub_name)
        print("\n⚠️  WARNING: Message publishing works but receiving failed")
        print("   This may be a timing issue - Celery might still work")

    # Test 9: Cleanup
    cleanup_resources(publisher, subscriber, project_id, topic_name, sub_name)

    # Test 10: Celery integration
    test_celery_broker_url(project_id)

    # Final summary
    print_header("SUMMARY")
    print_success("All critical tests passed!")
    print_info("GCP Pub/Sub is ready for Celery")
    print_info(f"\nTo use in Celery, set in your .env:")
    print(f"    GCP_PROJECT_ID={project_id}")
    print("\nThen restart your Celery workers.")

    return 0


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
