# python-template：Python 项目模板

一个可复制的、基于 src/ 布局的 Python 3.14+ 项目模板。它是一个**仓库模板**，不是业务运行时：模板只提供项目边界、打包配置、严格类型检查、测试和 CI 基线，不预设领域实现。

> English summary: this repository is a self-contained, copy-first Python project template. It intentionally contains no product-specific runtime.

## 适用场景

复制本仓库后，可以快速得到一个具备以下基础设施的 Python 包：

- src/<package>/ 源码布局，避免未安装源码意外遮蔽已安装包；
- Hatchling 构建和可发布的 wheel/sdist；
- Python 3.14+、严格 BasedPyright 类型检查；
- Ruff 全规则 lint 与格式检查；
- pytest 测试入口和 importlib 隔离导入模式；
- py.typed 标记，默认面向类型化库；
- 可直接复制并修改的 GitHub Actions CI。

## 仓库布局

~~~text
.
├── .github/workflows/ci.yml  # 冻结依赖、lint、类型检查、测试和构建
├── src/
│   ├── README.md              # 源码扩展约定
│   └── python_template/       # 示例 Python 导入包名
│       ├── __init__.py
│       └── py.typed
├── tests/
│   ├── README.md              # 测试扩展约定
│   └── test_package.py        # 最小导入烟雾测试
├── docs/template-contract.md  # 模板契约和身份替换清单
├── AGENTS.md                  # 仓库本地开发规则
├── LICENSE
├── README.md
├── pyproject.toml
└── .python-version
~~~

根目录就是可复制的模板实例；不需要先安装模板引擎，也不会执行复制过程中的脚本。

## 创建新项目

### 方式一：复制仓库

~~~bash
cp -a python-template my-project
cd my-project
rm -rf .git
git init
~~~

也可以使用 GitHub 的 **Use this template**，或将本目录复制到一个新的空仓库。复制后按下面的清单替换身份。

### 方式二：保留模板作为基线

如果要在同一仓库维护多个包，不要把模板目录直接当作产品包发布；将它复制到独立的包目录，并让每个实例拥有自己的 pyproject.toml、src/<package>/ 和 tests/。

## 实例化清单

在第一次提交前，至少更新这些位置：

1. pyproject.toml 的 project.name、version、description、作者和许可证信息；
2. src/python_template/ 目录名以及所有导入语句，将其改成合法的 Python 导入名；
3. README.md、AGENTS.md、src/README.md 和 tests/README.md 中的项目身份；
4. .python-version 与 pyproject.toml 中的 Python 最低版本（如果项目需要不同版本）；
5. CI 中的发布、权限和项目特定步骤；
6. LICENSE 中的版权主体。

发行名可以包含连字符（例如 python-template），但导入包名必须是 Python 标识符（例如 python_template）。本模板因此使用发行名 python-template 和导入名 python_template；不要把二者混为一谈。

## src 与 extraPaths

Python 运行时不会因为目录叫 src 就自动把它加入 sys.path；开发时应通过 editable install 或构建后的包导入。模板在 [tool.pyright] 中显式保留：

~~~toml
extraPaths = ["src"]
~~~

这是给 Pyright/BasedPyright 的静态导入解析路径，不会改变 Python 运行时。现代 Pyright 通常也会对根目录下的 src 做回退探测，但显式配置能让编辑器、旧版本工具和 CI 获得一致结果。

## 开发

在仓库根目录执行：

~~~bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
~~~

推荐的验证顺序是 ruff check -> ruff format --check -> basedpyright -> pytest。

构建并检查发行包：

~~~bash
uv build
unzip -l dist/*.whl
~~~

wheel 应包含 python_template/__init__.py 和 python_template/py.typed，而不应把仓库根目录的测试或缓存带入运行时包。

## 模板原则

- **复制优先**：模板是静态、自包含的仓库，不依赖远程路径或执行钩子。
- **最小公共面**：示例包只有包标记，不伪造业务 API；实例化者按领域添加模块。
- **显式边界**：源码、测试、构建和文档路径都从当前仓库根解析。
- **可替换身份**：项目发行名、导入名、作者和版本都集中在实例化清单中。
- **默认安全**：不在安装或构建阶段运行任意脚本，不携带本地路径依赖。

## 从旧版本升级

当前仓库已从一个特定领域的运行时重置为通用项目模板。旧版本的领域 API 不再是模板公共契约；需要继续使用旧产品的项目应固定旧版本或迁移实现，不要把模板包当作兼容层。

## License

MIT，详见 [LICENSE](LICENSE)。
