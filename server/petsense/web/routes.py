"""网页工具静态路由：实验 dashboard、动作识别面板、上传媒体服务。"""

from flask import Blueprint, send_from_directory

from petsense import config

bp = Blueprint('web', __name__)


@bp.route('/uploads/<path:filename>')
def app_uploads(filename):
    return send_from_directory(config.UPLOADS_DIR, filename)


@bp.route('/action')
@bp.route('/action/')
def action_dashboard():
    return send_from_directory(config.ACTION_DASHBOARD_DIR, 'index.html')


@bp.route('/')
def index():
    return send_from_directory(str(config.RUNTIME_ROOT), 'dashboard.html')


@bp.route('/<path:path>')
def serve_spa(path):
    import os
    full = os.path.join(config.STATIC_DIR, path)
    if os.path.isfile(full):
        return send_from_directory(config.STATIC_DIR, path)
    return send_from_directory(str(config.RUNTIME_ROOT), 'dashboard.html')
