name: 缺陷报告 (Bug)
description: 报告一个 App 的问题
labels: ['bug']
body:

- type: textarea
  id: description
  attributes:
  label: 问题描述
  description: 清晰简洁地描述遇到的问题
  validations:
  required: true
- type: textarea
  id: steps
  attributes:
  label: 复现步骤
  description: 详细描述如何复现
  placeholder: | 1. 进入「宠物」页 2. 点击某只宠物卡片 3. 观察……
  validations:
  required: true
- type: textarea
  id: expected
  attributes:
  label: 期望行为
  validations:
  required: true
- type: textarea
  id: actual
  attributes:
  label: 实际行为
  validations:
  required: true
- type: textarea
  id: environment
  attributes:
  label: 环境信息
  description: 设备 / 模拟器型号、系统版本、App 版本等
  placeholder: 'iPhone 16 Pro 模拟器, iOS 18.2, Expo Go'
  validations:
  required: true
- type: textarea
  id: logs
  attributes:
  label: 截图 / 日志
  description: 如有报错日志或截图请附上
