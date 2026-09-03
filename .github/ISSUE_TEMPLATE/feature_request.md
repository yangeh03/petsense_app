name: 功能建议 (Feature)
description: 建议一个新功能或改进
labels: ['enhancement']
body:

- type: textarea
  id: problem
  attributes:
  label: 想解决什么问题？
  description: 这个功能解决了什么痛点？什么场景下需要？
  validations:
  required: true
- type: textarea
  id: solution
  attributes:
  label: 期望的方案
  description: 描述你期望的交互或效果，如有参考 App 可附截图
  validations:
  required: true
- type: textarea
  id: alternatives
  attributes:
  label: 备选方案
  description: 你考虑过的其他做法
