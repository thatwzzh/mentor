"""配置错误类型：区分「配置不存在」与「配置不可用」，供上层选择降级或 fail-fast。"""


class ConfigError(RuntimeError):
    """所有配置读取错误的基类。"""


class ConfigNotFoundError(ConfigError):
    """配置在配置中心不存在；有 last-known-good 时允许降级。"""


class ConfigUnavailableError(ConfigError):
    """配置中心不可用、鉴权失败或内容非法；必须 fail-fast。"""
