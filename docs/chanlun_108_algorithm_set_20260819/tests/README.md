# 测试说明

运行：

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/reference_oracle.py
```

`reference_oracle.py` 只实现合成fixtures所需的最小判断，用于锁定契约；它不是生产级行情解析器，也不输出实盘订单。

新增规则时至少加入：

1. 一个正例。
2. 一个最邻近反例。
3. 一个等号/最小间隔边界。
4. 一个未完成对象反例。
5. 一个确认时间晚于端点时间的回放例。

若修改标准口径，必须新增profile，而不是改写旧fixture的期望值。
