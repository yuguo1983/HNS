# fix_undefined_vars

当链接报错有未定义的变量/函数时，自动将 `libGD32F303VET6.a.bk` 复制覆盖为 `libGD32F303VET6.a`，用于强制使用备库完成链接。

## 触发条件
用户提到"未定义"、"undefined"、"链接错误"、"link error"、"undefined reference"时触发。

## 使用方式
Agent 调用此技能后，自动执行文件复制操作。