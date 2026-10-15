#!/usr/bin/env python3
"""PetSense 演示数据种子：向 /api/app/* 写入演示用户、宠物、帖子（PetGIF 真实行为动图）、点赞、评论与关注关系。

用法：python3 seed-demo.py [BASE，默认 http://localhost:8080]
幂等：主账号 petsense 能登录时跳过。
"""

import json
import sys
import urllib.error
import urllib.request

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
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read())


def form_request(method, path, token, fields):
    boundary = '----petsense-seed'
    parts = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(f'--{boundary}--\r\n'.encode())
    req = urllib.request.Request(
        BASE + path,
        data=b''.join(parts),
        method=method,
        headers={
            'X-Petsense-Session': token,
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
    )
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read())


USERS = [
    {
        # 演示主账号：与 EXPO_PUBLIC_DEV_AUTOLOGIN 的自动登录账号一致
        'username': 'petsense',
        'display_name': 'Max 爸爸',
        'password': '123456',
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

# 帖子媒体：PetGIF default 演示宠物的真实行为动图（外部服务公开存储）
POSTS = [
    {
        'author': 0,
        'caption': '今天带 Max 去滨江公园撒欢，跑了一下午都不肯回家 🐕 #公园日 #快乐狗狗',
        'location': '滨江公园',
        'behavior': 'run',
    },
    {
        'author': 1,
        'caption': '豆豆的短腿冲刺速度还是很能打的！柯基冲刺训练day 21 💪 #柯基 #遛狗日常',
        'location': '小区草坪',
        'behavior': 'walk',
    },
    {
        'author': 2,
        'caption': '年糕终于肯睡在我键盘上了，傲娇猫的认可 🐱 #英短 #猫咪日常',
        'location': '家里书房',
        'behavior': 'sleep',
    },
    {
        'author': 3,
        'caption': '雪球今天吃了一整根提摩西草，干饭兔实锤 🐰 #垂耳兔 #干饭',
        'location': '宠物医院门口',
        'behavior': 'eat',
    },
    {
        'author': 0,
        'caption': '晨跑五公里，Max 的配速比我还稳 😅 #晨跑 #金毛',
        'location': '江边步道',
        'behavior': 'walk',
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

    main_token = tokens[0]
    main_id = user_ids[0]

    # PetGIF default 演示宠物的真实行为动图
    gifs = call('GET', '/petgif/pet/default')['pet']['behaviors']

    post_ids = []
    for index, post in enumerate(POSTS):
        gif_url = gifs.get(post['behavior'], {}).get('gifUrl')
        post_ids.append(
            form_request(
                'POST', '/posts', tokens[post['author']],
                {
                    'caption': post['caption'],
                    'location': post['location'],
                    'pet_id': str(pet_ids[post['author']]),
                    'media_url': gif_url or '',
                    'media_kind': 'gif' if gif_url else '',
                },
            )['postId']
        )

    # 互相点赞 + 评论
    for post_index, post_id in enumerate(post_ids):
        for commenter in range(len(tokens)):
            call('POST', f'/posts/{post_id}/like', tokens[commenter])
            if commenter != post_index % len(tokens):
                call('POST', f'/posts/{post_id}/comments', tokens[commenter],
                     {'body': COMMENTS[(post_index + commenter) % len(COMMENTS)]})

    # 关注关系：其他人 → 主账号；主账号 → 其他人（跳过自己）
    for index in range(1, len(tokens)):
        call('POST', f'/users/{main_id}/follow', tokens[index])
    for index in range(1, len(tokens)):
        call('POST', f'/users/{user_ids[index]}/follow', main_token)

    feed = call('GET', '/feed', main_token)
    print(f"种子完成：{len(feed['posts'])} 篇帖子，{len(tokens)} 个演示账号（主账号 {USERS[0]['username']} / {USERS[0]['password']}）")


if __name__ == '__main__':
    main()
