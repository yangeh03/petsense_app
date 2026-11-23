"""PetSense 服务端应用工厂。

用法：
    from petsense.app import create_app
    app = create_app()
"""

import os

from flask import Flask, request

from petsense import config, db
from petsense.actions import service as actions_service
from petsense.auth import routes as auth_routes
from petsense.devices import routes as devices_routes
from petsense.experiments import recorder as experiments_recorder
from petsense.petgif import daily_diary, proxy as petgif_proxy
from petsense.pets import routes as pets_routes
from petsense.social import routes as social_routes
from petsense.telemetry import audio as telemetry_audio
from petsense.telemetry import edge as telemetry_edge
from petsense.telemetry import observations
from petsense.telemetry import commands as telemetry_commands
from petsense.telemetry import mqtt_ingest
from petsense.telemetry import realtime as telemetry_realtime
from petsense.trends import routes as trends_routes
from petsense.web import routes as web_routes
from petsense.uwb import routes as uwb_routes
from petsense.uwb import setup as uwb_setup


def create_app(start_background=True):
    """创建 Flask 应用。

    start_background=False 供测试使用：不连 MQTT、不启动 WS 心跳线程。
    """
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    os.makedirs(config.CLOUD_DATA_DIR, exist_ok=True)
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    os.makedirs(config.SOCIAL_MEDIA_DIR, exist_ok=True)
    db.init_db()
    observations.backfill()

    from petsense.agent.routes import bp as agent_bp
    from petsense.agent.inbox_routes import bp as agent_inbox_bp
    app = Flask('petsense')
    app.register_blueprint(agent_bp)
    app.register_blueprint(agent_inbox_bp)  # W12：收件箱（独立 blueprint，不与 agent/routes.py 冲突）
    app.register_blueprint(mqtt_ingest.collection_store.blueprint())
    telemetry_realtime.sock.init_app(app)
    app.register_blueprint(telemetry_realtime.bp)
    app.register_blueprint(actions_service.bp)
    app.register_blueprint(telemetry_audio.bp)
    app.register_blueprint(telemetry_edge.bp)
    app.register_blueprint(observations.bp)
    app.register_blueprint(telemetry_commands.bp)
    app.register_blueprint(experiments_recorder.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(pets_routes.bp)
    app.register_blueprint(devices_routes.bp)
    app.register_blueprint(social_routes.bp)
    app.register_blueprint(petgif_proxy.bp)
    app.register_blueprint(daily_diary.bp)
    app.register_blueprint(trends_routes.bp)
    app.register_blueprint(web_routes.bp)
    app.register_blueprint(uwb_routes.bp)
    app.register_blueprint(uwb_setup.bp)

    @app.after_request
    def app_cors_headers(response):
        origin = request.headers.get('Origin')
        if origin and (request.path.startswith('/api/app/') or request.path.startswith('/api/action/')
                       or request.path.startswith('/uploads/')):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = f'{config.APP_TOKEN_HEADER}, Content-Type, Idempotency-Key, If-Match'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response.headers['Vary'] = 'Origin'
        return response

    @app.teardown_appcontext
    def _close_db_session(_exception):
        db.Session.remove()

    if start_background:
        from petsense.social.jobs import start_social_worker
        start_social_worker()
        from petsense.petgif.diary_jobs import recover_diary_jobs
        recover_diary_jobs()
        from petsense.agent.memory_jobs import start_memory_worker
        start_memory_worker()  # W04：日/周汇总、认识卡蒸馏、记忆报告（幂等 tick）
        from petsense.agent import jobs as agent_jobs
        from petsense.agent import tasks as agent_tasks
        agent_jobs.register_handler(agent_tasks.CHECKPOINT_KIND, agent_tasks.handle_checkpoint_job)
        agent_jobs.start_scheduler()  # W08：持久作业调度（PETSENSE_SCHEDULER_DISABLED 可关；kind 执行器随各域注册）
        from petsense.agent import care_jobs
        care_jobs.start_care()  # W09：scan/review/event 执行器注册 + 周期扫描入队（幂等）
        from petsense.agent import inbox_jobs
        inbox_jobs.start_inbox()  # W12：收件箱投影作业（注册 + 周期入队，幂等）
        from petsense.agent import ws as agent_ws
        agent_ws.start_ping_loop()
        telemetry_realtime.start_ws_ping_loop()
        mqtt_ingest.start_mqtt()
    return app
