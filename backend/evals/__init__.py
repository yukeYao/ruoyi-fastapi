"""
智能助手评估（手动运行，不进 pytest/CI）。入口：python -m evals.run_eval，说明见 evals/README.md。
dataset / scoring / report 不依赖数据库和模型，可以被离线测试直接导入；
environment / runners 会连接测试库，只能经 run_eval 在设置好测试环境变量后导入。
"""
