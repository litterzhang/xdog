# 历史计划、审计与验收归档

这些记录保留原来的日期、范围、版本、判断和证据，包括未完成或相互替代的方案。
**归档不是兼容承诺，不是当前待办，也不证明当前版本已经通过记录中的测试。**
历史命令、类名、源码路径可能已删除；不要从归档恢复权限、重放工具或重新创建服务。
可导航的相对链接和显式文档路径已随目录移动更新，原测试数字和结论没有改写。
当前用法与设计统一见 [文档入口](../README.md) 和 [架构](../architecture.md)。

## 分类索引

- [agent](agent/)：15 份记录
- [agent-ui](agent-ui/)：51 份记录
- [ci](ci/)：2 份记录
- [claw](claw/)：42 份记录
- [coding](coding/)：4 份记录
- [engineering](engineering/)：16 份记录
- [eyes](eyes/)：7 份记录
- [flow](flow/)：3 份记录
- [goal](goal/)：19 份记录
- [plans](plans/)：7 份记录
- [superpowers](superpowers/)：34 份记录
- [tui](tui/)：3 份记录

## 查找旧记录

旧顶层文件按主题移动后保留原文件名，可用文件名查找，例如：

```bash
find docs/archive -name '*goal-simple*'
rg -n 'GR-01|GO-01|UI-001' docs/archive
```

此前 `plans/`、`superpowers/` 和 `ci/` 保留其内部层级；不为旧路径保留转发文件。
