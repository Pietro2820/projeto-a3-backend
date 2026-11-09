import os
from celery import Celery
from kombu import Queue, Exchange

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "projeto_a3",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.queue.tasks"],
)

default_exchange = Exchange("default", type="direct")

celery_app.conf.update(
    task_queues=(
        Queue("uploads", default_exchange, routing_key="uploads"),
        Queue("transcode", default_exchange, routing_key="transcode"),
        Queue("notifications", default_exchange, routing_key="notifications"),
    ),
    task_default_queue="uploads",
    task_default_exchange="default",
    task_default_routing_key="uploads",
    task_routes={
        "app.queue.tasks.processar_upload": {"queue": "uploads", "routing_key": "uploads"},
        "app.queue.tasks.transcodificar_video": {"queue": "transcode", "routing_key": "transcode"},
        "app.queue.tasks.notificar_status": {"queue": "notifications", "routing_key": "notifications"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)