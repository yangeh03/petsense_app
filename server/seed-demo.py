#!/usr/bin/env python3
"""PetSense 演示数据种子：向 /api/app/* 写入演示用户、宠物、帖子、点赞、评论与关注关系。

用法：python3 seed-demo.py [BASE，默认 http://localhost:8080]
幂等：检测到 seed_max 账号已存在时跳过。
"""

import json
import struct
import sys
import urllib.error
import urllib.request
import zlib

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080') + '/api/app'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, token=None, data=None):
    headers = {}
    body = None
    if token:
        headers['X-Petsense-Session'] = token
    if data is not None:
        body = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read())


def png_bytes(width, height, top, bottom):
    """生成上下渐变的占位图（演示用）。"""
    def chunk(tag, payload):
        body = tag + payload
        return struct.pack('>I', len(payload)) + body + struct.pack('>I', zlib.crc32(body))

    rows = b''.join(
        b'\x00' + bytes(
            round(top[i] + (bottom[i] - top[i]) * (y / (height - 1))) for i in range(3)
        ) * width
        for y in range(height)
    )
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(rows))
        + chunk(b'IEND', b'')
    )


def multipart(fields, image):
    boundary = '----petsense-seed'
    parts = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="photo.png"\r\n'
        f'Content-Type: image/png\r\n\r\n'.encode()
        + image
        + b'\r\n'
    )
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


USERS = [
    {
        # 演示主账号：与 EXPO_PUBLIC_DEV_AUTOLOGIN 的自动登录账号一致
        'username': 'e2e_max_dad',
        'display_name': 'Max 爸爸',
        'password': 'secret66',
        'avatarEmoji': '🧑‍🌾',
        'pet': {'name': 'Max', 'species': 'dog', 'breed': '金毛寻回犬', 'avatarEmoji': '🦮', 'deviceId': 'esp32-001'},
    },
    {
        'username': 'seed_doudou',
        'display_name': '豆豆妈妈',
        'password': 'demo123',
        'avatarEmoji': '👩‍🦰',
        'pet': {'name': '豆豆', 'species': 'dog', 'breed': '柯基', 'avatarEmoji': '🐶', 'deviceId': ''},
    },
    {
        'username': 'seed_niangao',
        'display_name': '年糕姐姐',
        'password': 'demo123',
        'avatarEmoji': '👩‍💻',
        'pet': {'name': '年糕', 'species': 'cat', 'breed': '英短蓝猫', 'avatarEmoji': '🐱', 'deviceId': ''},
    },
    {
        'username': 'seed_xueqiu',
        'display_name': '雪球同学',
        'password': 'demo123',
        'avatarEmoji': '🧑‍🎓',
        'pet': {'name': '雪球', 'species': 'rabbit', 'breed': '垂耳兔', 'avatarEmoji': '🐰', 'deviceId': ''},
    },
]

POSTS = [
    {
        'author': 0,
        'caption': '今天带 Max 去滨江公园撒欢，跑了一下午都不肯回家 🐕 #公园日 #快乐狗狗',
        'location': '滨江公园',
        'color_top': (247, 202, 160),
        'color_bottom': (232, 131, 92),
    },
    {
        'author': 1,
        'caption': '豆豆的短腿冲刺速度还是很能打的！柯基冲刺训练day 21 💪 #柯基 #遛狗日常',
        'location': '小区草坪',
        'color_top': (250, 224, 170),
        'color_bottom': (240, 168, 90),
    },
    {
        'author': 2,
        'caption': '年糕终于肯睡在我键盘上了，傲娇猫的认可 🐱 #英短 #猫咪日常',
        'location': '家里书房',
        'color_top': (190, 214, 232),
        'color_bottom': (120, 160, 190),
    },
    {
        'author': 3,
        'caption': '雪球今天吃了一整根提摩西草，干饭兔实锤 🐰 #垂耳兔 #干饭',
        'location': '宠物医院门口',
        'color_top': (214, 232, 210),
        'color_bottom': (150, 190, 160),
    },
    {
        'author': 0,
        'caption': '晨跑五公里，Max 的配速比我还稳 😅 #晨跑 #金毛',
        'location': '江边步道',
        'color_top': (252, 236, 210),
        'color_bottom': (245, 180, 130),
    },
]

COMMENTS = ['太可爱了！', '毛色真好 😍', '下次一起遛狗呀', '同款狗子路过', '拍得好好看！']


def main():
    # 幂等：主账号能登录说明已播种，跳过
    try:
        call('POST', '/auth/login', data={'username': USERS[0]['username'], 'password': USERS[0]['password']})
        print('种子数据已存在，跳过')
        return
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise

    tokens = []
    user_ids = []
    pet_ids = []
    for index, user in enumerate(USERS):
        data = call('POST', '/auth/register', data={
            'username': user['username'],
            'display_name': user['display_name'],
            'password': user['password'],
            'avatarEmoji': user['avatarEmoji'],
        })
        tokens.append(data['token'])
        user_ids.append(data['user']['id'])
        pet = call('POST', '/pets', tokens[-1], user['pet'])['pet']
        pet_ids.append(pet['id'])
        print(f"  用户 {user['display_name']}(id={data['user']['id']}) + 宠物 {pet['name']}(id={pet['id']})")

    main_id, main_token = user_ids[0], tokens[0]

    # 关注关系：其他人 → 主账号；主账号 → 其他人（跳过自己）
    for index in range(1, len(tokens)):
        call('POST', f'/users/{main_id}/follow', tokens[index])
    for index in range(1, len(tokens)):
        call('POST', f'/users/{user_ids[index]}/follow', main_token)

    post_ids = []
    for index, post in enumerate(POSTS):
        image = png_bytes(640, 480, post['color_top'], post['color_bottom'])
        body, content_type = multipart(
            {
                'caption': post['caption'],
                'location': post['location'],
                'pet_id': str(pet_ids[post['author']]),
            },
            image,
        )
        req = urllib.request.Request(
            BASE + '/posts', data=body, method='POST',
            headers={'X-Petsense-Session': tokens[post['author']], 'Content-Type': content_type},
        )
        with opener.open(req, timeout=10) as resp:
            post_ids.append(json.loads(resp.read())['postId'])

    # 互相点赞 + 评论
    for post_index, post_id in enumerate(post_ids):
        for commenter in range(len(tokens)):
            call('POST', f'/posts/{post_id}/like', tokens[commenter])
            if commenter != post_index % len(tokens):
                call('POST', f'/posts/{post_id}/comments', tokens[commenter],
                     {'body': COMMENTS[(post_index + commenter) % len(COMMENTS)]})

    feed = call('GET', '/feed', main_token)
    print(f"种子完成：{len(feed['posts'])} 篇帖子，{len(tokens)} 个演示账号（主账号 {USERS[0]['username']} / {USERS[0]['password']}）")


if __name__ == '__main__':
    main()
