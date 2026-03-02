# ARM SVE 自动向量化工具

基于 Python 的 C 代码自动向量化工具，将普通 C 循环转换为使用 ARM SVE（Scalable Vector Extension，可扩展向量扩展）intrinsics 的高性能 C 代码。

---

## 目录

1. [工具链概览](#1-工具链概览)
2. [快速上手](#2-快速上手)
3. [模块详解](#3-模块详解)
   - [CLoopExtraction.py — 循环提取](#31-cloopextractionpy--循环提取)
   - [LoopAnalyzer.py — 模式分析](#32-loopanalyzerpy--模式分析)
   - [LoopUnroller.py — 展开决策](#33-loopunrollerpy--展开决策)
   - [SVECodeGen.py — 代码生成](#34-svecodegenpy--代码生成)
   - [SVEVectorizer.py — 主入口](#35-svevectorizerpy--主入口)
4. [支持的向量化模式](#4-支持的向量化模式)
5. [ARM SVE Intrinsics 映射表](#5-arm-sve-intrinsics-映射表)
6. [未实现功能（待扩展）](#6-未实现功能待扩展)
7. [使用示例](#7-使用示例)
8. [测试](#8-测试)
   - [单元测试](#运行测试套件)
   - [TSVC Benchmark 端到端测试](#tsvc-benchmark-端到端测试)

---

## 1. 工具链概览

### 数据流

```
C 源文件 (.c)
      │
      ▼
┌─────────────────────┐
│  CLoopExtraction.py │  pycparser AST 解析 → 提取循环结构
│  extract_loops()    │  输出: List[LoopInfo]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  LoopAnalyzer.py    │  模式识别 / 依赖分析 / 类型推断
│  analyze_loops()     │ 输出: List[AnalyzedLoop]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  LoopUnroller.py    │  展开因子决策 / 预取策略决策
│  unroll_loops()     │  输出: List[(AnalyzedLoop, UnrollConfig)]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  SVECodeGen.py      │  按模式 + UnrollConfig 生成三段式 SVE 代码
│  generate_sve_code()│  输出: str (SVE C 代码片段)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  SVEVectorizer.py   │  将代码片段按行号嵌回原文件
│  SVEVectorizer.run()│  注入头文件，写出 *_sve.c
└─────────────────────┘
      │
      ▼
输出文件 (*_sve.c)
```

### 文件一览

| 文件 / 目录 | 状态 | 职责 |
|------------|------|------|
| `CLoopExtraction.py` | ✅ 已实现 | pycparser AST 解析，提取 `LoopInfo` |
| `LoopAnalyzer.py` | ✅ 已实现 | 模式分类、依赖检查、类型推断 |
| `LoopUnroller.py` | ✅ 已实现 | 展开因子与预取策略决策，输出 `UnrollConfig` |
| `SVECodeGen.py` | ✅ 已实现 | ELEMENTWISE/REDUCTION/CONDITIONAL 三类主路径代码生成（三段式） |
| `SVEVectorizer.py` | ✅ 已实现 | CLI 主入口，管道编排，行号替换，头文件注入，阶段文件保存 |
| `tests/run_tests.py` | ✅ 已实现 | 32 个自动化测试，全部通过 |
| `stages/` | 运行时生成 | `--save-stages` 输出目录，保存各阶段中间结果 |
| `TSVC_2/` | ✅ 已集成 | TSVC benchmark 源码及预处理文件，用于端到端测试 |

### 依赖

| 依赖 | 用途 |
|------|------|
| `pycparser` | C 语言 AST 解析 |
| `gcc`（可选） | C 预处理（处理 `#include`/`#define`） |
| Python ≥ 3.8 | 标准库 |

---

## 2. 快速上手

### 唯一入口

**`SVEVectorizer.py` 是整个工具链的统一入口**，用户无需直接调用其他模块。

```
SVEVectorizer.py          ← 用户入口（命令行 / Python API）
├── CLoopExtraction.py    ← 内部模块，由 SVEVectorizer 自动调用
├── LoopAnalyzer.py       ← 内部模块
├── LoopUnroller.py       ← 内部模块
└── SVECodeGen.py         ← 内部模块
```

### 使用方式一：命令行（最常用）

```bash
# 最简用法：输出到 input_sve.c
python SVEVectorizer.py input.c

# 指定输出文件
python SVEVectorizer.py input.c -o output.c

# 文件无系统头文件时加 --no-cpp（速度更快，测试文件均适用）
python SVEVectorizer.py input.c --no-cpp

# 查看分析报告（不写文件）
python SVEVectorizer.py input.c --no-cpp --dry-run --report

# 保存各阶段中间结果到 stages/ 目录
python SVEVectorizer.py input.c --no-cpp --save-stages

# 完整选项
python SVEVectorizer.py input.c --no-cpp --verbose --report --unroll 4 --prefetch-dist 16

```

**所有 CLI 选项**：

| 选项 | 默认 | 说明 |
|------|------|------|
| `input` | 必填 | 输入 C 源文件路径 |
| `-o <file>` | `<input>_sve.c` | 输出文件路径 |
| `--no-cpp` | 关闭 | 不调用 gcc 预处理（无系统头文件时使用，速度更快） |
| `--verbose` / `-v` | 关闭 | 打印每个循环的分析过程和展开决策 |
| `--dry-run` | 关闭 | 仅分析，不写出文件 |
| `--report` | 关闭 | 打印向量化统计报告到 stdout |
| `--unroll N` | `0`（自动） | 强制指定展开因子；0 = 由 LoopUnroller 自动决策 |
| `--prefetch-dist N` | `0`（自动） | 强制指定软件预取距离（单位：vl）；0 = 自动 |
| `--save-stages` | 关闭 | 保存四个阶段文件到 `stages/` 目录 |

### 使用方式二：Python API

当需要在脚本或其他工具中调用时，直接实例化 `SVEVectorizer` 类：

```python
from SVEVectorizer import SVEVectorizer

v = SVEVectorizer(verbose=True)
success = v.run(
    input_file  = 'my_kernel.c',
    output_file = 'my_kernel_sve.c',
    use_cpp     = False,       # 等价于 --no-cpp
    dry_run     = False,
    print_report= True,        # 等价于 --report
    unroll_factor = 0,         # 0 = 自动决策
    prefetch_dist = 0,         # 0 = 自动决策
    save_stages = True,        # 等价于 --save-stages
)
print('向量化', '成功' if success else '失败')
```

### 典型工作流

```
1. 准备 C 文件
        ↓
2. python SVEVectorizer.py input.c --no-cpp --dry-run --report
   → 确认哪些循环能被向量化，哪些被跳过及原因
        ↓
3. python SVEVectorizer.py input.c --no-cpp --save-stages
   → 生成 input_sve.c，同时在 stages/ 查看各阶段细节
        ↓
4. 用 ARM 工具链编译验证
   aarch64-linux-gnu-gcc -O2 -march=armv8-a+sve input_sve.c -o input_sve
```

---

## 3. 模块详解

### 3.1 CLoopExtraction.py — 循环提取

**职责**：使用 pycparser 将 C 源文件解析为 AST，从中提取所有 `for` 循环的完整结构信息。是整个工具链的**数据源头**。

#### 对外接口

```python
def extract_loops(c_file: str, use_cpp: bool = True) -> List[LoopInfo]
```

| 参数 | 说明 |
|------|------|
| `c_file` | C 源文件路径 |
| `use_cpp` | 是否调用 `gcc -E` 预处理（含系统头文件时需开启） |

#### 核心数据结构

```
LoopInfo                         ← 单个循环的完整描述
├── 控制信息
│   ├── loop_var       str       循环变量名，e.g. "i"
│   ├── loop_start     str       初始值，e.g. "0"
│   ├── loop_end       str       终止值，e.g. "n"
│   ├── loop_step      int       步长（0 = 未知/非常量）
│   └── loop_end_op    str       比较符，"<" / "<=" / "!="
│
├── 源位置（用于行号回写）
│   ├── node_coord_start  int    循环起始行（1-based）
│   ├── node_coord_end    int    循环结束行（递归估算）
│   └── raw_source_lines  List   原始 C 代码行
│
├── 数组访问
│   ├── array_reads    List[ArrayAccess]   所有右值数组访问
│   └── array_writes   List[ArrayAccess]   所有左值数组访问
│
├── 操作语义
│   ├── body_operator  OperatorKind        ADD/SUB/MUL/DIV/ASSIGN
│   ├── is_reduction   bool
│   ├── reduction_var  str                 归约变量名
│   └── reduction_op   OperatorKind        归约操作类型
│
├── 条件分支
│   ├── has_condition  bool
│   └── condition_info ConditionInfo       比较符、比较值、分支描述
│
├── 嵌套关系
│   ├── nesting_level  int       0 = 最外层
│   ├── parent_loop_var str      父循环变量名
│   └── inner_loops    List[LoopInfo]
│
└── 类型上下文
    └── type_context   Dict[str,str]  {"a": "float*", "n": "int"}
```

```
ArrayAccess                      ← 单次数组访问
├── array_name    str            数组名，e.g. "a"
├── index_vars    List[str]      下标变量，e.g. ["i"] 或 ["i","N","j"]
├── index_offset  int            常量偏移，a[i+2] → 2
├── is_write      bool           True=左值(写)，False=右值(读)
├── pattern       AccessPattern  INDEXED_LINEAR / INDEXED_2D / INDEXED_OFFSET
└── c_type        str            元素类型（若能从声明推断）
```

#### 内部实现要点

| 组件 | 功能 |
|------|------|
| `LoopExtractor(NodeVisitor)` | 遍历 AST，`visit_For` 处理每个 for 循环，`visit_Decl` 收集类型上下文 |
| `_BodyAnalyzer` | 分析循环体，提取数组读写、操作符、归约模式、条件分支 |
| `_strip_c_comments()` | 用正则剥离 `/* */` 和 `//` 注释（`use_cpp=False` 时 pycparser 不支持注释） |
| `_estimate_end_line()` | 递归遍历 AST 子树取最大行号（pycparser 不直接提供循环结束行） |
| `_parse_subscript()` | 解析下标表达式，支持 `+`/`-`/`*`，从 `i*N+j` 中正确提取所有变量名 |
| `_build_nesting_tree()` | 按行号范围将扁平循环列表建立父子嵌套关系 |

**已解决的关键问题**：
- `a[i] = b[i] + c[i]` 的运算符在 RHS 的 `BinaryOp.op` 中，需从右值提取，而非从赋值符号提取
- `i*N+j` 下标中的 `i` 必须在 `_parse_subscript` 的 `*` 分支中递归提取，才能正确识别矩阵访问模式

---

### 3.2 LoopAnalyzer.py — 模式分析

**职责**：对 `LoopInfo` 列表进行**模式识别**、**可向量化性判断**、**数据类型推断**，输出 `AnalyzedLoop`。

#### 对外接口

```python
def analyze_loops(loops: List[LoopInfo]) -> List[AnalyzedLoop]
```

只分析最内层循环（`len(inner_loops) == 0`）。

#### 核心数据结构

```
AnalyzedLoop
├── original            LoopInfo              原始提取信息
├── pattern             LoopPattern           识别出的模式
├── status              VectorizabilityStatus 可向量化状态
├── data_type           DataType              主操作数类型
├── operator            OperatorKind          主操作类型
├── dependency          DependencyInfo        依赖分析结果
├── sve_element_type    str    "float" / "double" / "int32_t" / "int64_t"
├── sve_type_suffix     str    "_f32" / "_f64" / "_s32" / "_s64" / "_u32"
├── vl_hint             str    "svcntw()" / "svcntd()"
├── whilelt_suffix      str    "b32" / "b64"
├── reduction_init_val  str    "0.0f" / "0.0" / "0"
└── rejection_reason    str    不可向量化时的说明
```

#### 模式分类规则（优先级从高到低）

| 优先级 | 模式 | 判断条件 |
|--------|------|---------|
| 1 | `REDUCTION` | `is_reduction == True`，存在归约变量 |
| 2 | `CONDITIONAL` | `has_condition == True`，循环体主体为 `if` 语句 |
| 3 | `ELEMENTWISE` | 写数组下标只含当前循环变量，无循环携带依赖 |
| 4 | `UNKNOWN` | 以上均不满足 |

#### 可向量化性检查规则

| 规则 | 触发条件 | 结果 |
|------|---------|------|
| 非单位步长 | `loop_step != 1` 或 `loop_step == 0` | NOT_VECTORIZABLE |
| RAW 依赖 | 写 `a[i]` 同时读 `a[i+k]`（k>0） | NOT_VECTORIZABLE |
| 别名偏移依赖 | 写数组出现在读数组中且有常量偏移 | NOT_VECTORIZABLE |
| 条件控制依赖 | 条件分支内修改循环控制变量 | NOT_VECTORIZABLE |

#### 类型推断映射

| C 类型字符串 | DataType | `sve_type_suffix` | `vl_hint` |
|------------|---------|-------------------|-----------|
| `float` / `float*` | FLOAT32 | `_f32` | `svcntw()` |
| `double` / `double*` | FLOAT64 | `_f64` | `svcntd()` |
| `int` / `int32_t` | INT32 | `_s32` | `svcntw()` |
| `long` / `int64_t` | INT64 | `_s64` | `svcntd()` |
| `unsigned int` / `uint32_t` | UINT32 | `_u32` | `svcntw()` |

---

### 3.3 LoopUnroller.py — 展开决策

**职责**：独立的循环展开策略模块。对每个 `AnalyzedLoop` 分析其模式和访存特征，决定**展开因子**和**软件预取策略**，输出 `UnrollConfig`，由 `SVECodeGen` 消费以生成三段式展开代码。

将展开决策与代码生成分离，使两者可以独立演进和测试。

#### 对外接口

```python
def unroll_loops(
    analyzed: List[AnalyzedLoop],
    unroll_factor: int  = 0,         # 0 = 自动决策；> 0 = 强制覆盖
    prefetch_dist: int  = 0,         # 0 = 自动决策；> 0 = 强制覆盖（单位：vl）
    enable_prefetch: bool | None = None,  # None = 自动；True/False = 强制覆盖
) -> List[Tuple[AnalyzedLoop, UnrollConfig]]
```

#### UnrollConfig 数据结构

```python
@dataclass
class UnrollConfig:
    unroll_factor:   int   # 主循环每次处理的向量份数（1 = 不展开）
    enable_prefetch: bool  # 是否在主循环插入 svprfd 软件预取
    prefetch_dist:   int   # 预取距离，单位为 vl（建议 4~16）
    reason:          str   # 决策说明，用于 --verbose 输出
```

#### 自动决策规则

| 模式 | 读数组数 | 展开因子 | 软件预取 | 原因 |
|------|---------|---------|---------|------|
| ELEMENTWISE | ≥ 2 | **2** | **开启**，8×vl | 访存密集，预取收益明显 |
| ELEMENTWISE | = 1 | **2** | 关闭 | 展开提升 ILP，单读无需预取 |
| REDUCTION | 任意 | **1**（不展开） | 关闭 | 累加器有顺序依赖 |
| 其他/不可向量化 | — | **1** | 关闭 | 保守策略 |

CLI 通过 `--unroll N` 和 `--prefetch-dist N` 可强制覆盖自动决策。

---

### 3.4 SVECodeGen.py — 代码生成

**职责**：根据 `AnalyzedLoop` 的模式、类型信息及 `UnrollConfig`，从 intrinsics 映射表中选取函数，生成完整的 SVE C 代码片段。

对于 ELEMENTWISE 模式，生成**三段式结构**，消除绝大多数 `svwhilelt` 调用开销。

#### 对外接口

```python
def generate_sve_code(loop: AnalyzedLoop,
                      cfg: Optional[UnrollConfig] = None) -> str
```

不可向量化时自动调用 `_gen_fallback()`，保留原始代码并附加跳过原因注释。

#### 三段式结构（ELEMENTWISE 核心优化）

```
┌─────────────────────────────────────────────┐
│ 主循环  条件: i + U*vl ≤ n                  │
│  谓词：svptrue（全真，无计算开销）            │
│  内容：U 份 load→compute→store + svprfd 预取 │
│  覆盖：floor(n / (U*vl)) 组，即绝大多数迭代  │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ 清理循环  条件: i + vl ≤ n （U>1 时存在）   │
│  谓词：svptrue（仍无开销）                   │
│  内容：1 份 load→compute→store               │
│  覆盖：展开对齐的余量（最多 U-1 次）          │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ 尾部  条件: i < n                            │
│  谓词：svwhilelt（最多执行 1 次）             │
│  内容：1 份带谓词的 load→compute→store       │
│  覆盖：不足一个向量宽度的剩余元素             │
└─────────────────────────────────────────────┘
```

**关键收益**：`svwhilelt` 从每次迭代都调用 → **最多调用 1 次**。

#### `_one_vec_block()` 辅助函数

将单份 load → compute → store 的生成逻辑提取为独立私有函数，主循环的 U 次展开、清理循环、尾部三处均调用此函数，避免重复代码：

```python
def _one_vec_block(self, loop, pg_var, offset_expr, suffix) -> List[str]
```

| 参数 | 说明 |
|------|------|
| `pg_var` | 谓词变量名，主/清理循环传 `"pg_all"`，尾部传 `"pg"` |
| `offset_expr` | 当前 i 的表达式，展开第 u 份传 `"i + u*vl"` |
| `suffix` | 变量名后缀，展开时用 `"_0"/"_1"` 区分，单份传 `""` |

#### Intrinsics 映射表

```python
_ARITH_X   # 算术运算（_x 形式，don't-care，纯计算优先使用）
_ARITH_M   # 算术运算（_m 形式，merge，归约累加使用）
_LOAD      # 连续加载   svld1_f32 / svld1_f64 / svld1_s32 ...
_STORE     # 连续存储   svst1_f32 / svst1_f64 / svst1_s32 ...
_REDUCE    # 水平归约   svaddv_f32 / svaddv_f64 / svaddv_s32 ...
_MLA       # 乘加 FMA   svmla_f32_x / svmla_f64_x ...
_CMP       # 比较谓词   svcmpgt / svcmplt / svcmpge / svcmple ...
_DUP       # 标量广播   svdup_f32 / svdup_f64 / svdup_s32 ...
_WHILELT   # 循环谓词   svwhilelt_b32 / svwhilelt_b64
_VEC_TYPE  # 向量类型   svfloat32_t / svfloat64_t / svint32_t ...
```

#### 各模式生成策略

| 模式 | 关键 intrinsics | 展开支持 |
|------|----------------|---------|
| ELEMENTWISE | `svptrue` + `svld1` + `svadd/sub/mul/div_x` + `svst1` + `svwhilelt`（尾部） | ✅ 三段式 + 展开 + 预取 |
| REDUCTION | `svdup` + `svld1` + `svadd_m` + `svaddv` + `svwhilelt` | 接口兼容（不展开） |
| CONDITIONAL | `svwhilelt` + `svld1` + `svcmpXX` + `cond_pg` + `svst1` | — |

> 说明：矩阵专用代码生成分支已在当前版本下线，后续将以独立模块形式逐步恢复。

#### 谓词形式选择原则

| 形式 | 场景 |
|------|------|
| `_x`（don't-care） | 纯计算运算，不活跃通道值不确定，**优先使用** |
| `_m`（merge） | 归约累加，不活跃通道保持第一操作数（累加器） |
| `_z`（zero） | 谨慎使用，置零可能引入误差 |

---

### 3.5 SVEVectorizer.py — 主入口

**职责**：CLI 接口 + 管道编排，协调四个子模块完成完整向量化流程，将 SVE 代码片段按行号嵌入原始文件并写出结果。

#### CLI 接口

```
python SVEVectorizer.py <input.c> [选项]

选项：
  -o <output.c>        输出路径（默认：<input>_sve.c）
  --no-cpp             不调用 C 预处理器（文件无系统头文件时使用）
  --verbose / -v       打印每个循环的分析过程和展开决策
  --dry-run            仅分析，不写出文件
  --report             打印向量化分析报告到 stdout
  --unroll N           强制覆盖展开因子（0 = 自动决策，默认）
  --prefetch-dist N    强制覆盖预取距离，单位 vl（0 = 自动决策，默认）
  --save-stages        保存各阶段中间结果到 stages/ 目录
```

#### 管道步骤

```
1. 读取源文件所有行
2. extract_loops()          → List[LoopInfo]          [可选] 保存 stage1_loops.txt
3. analyze_loops()          → List[AnalyzedLoop]       [可选] 保存 stage2_analyzed.txt
4. unroll_loops()           → List[(AnalyzedLoop, UnrollConfig)]
                                                        [可选] 保存 stage3_unroll.txt
5. codegen.generate(al,cfg) → List[str]（SVE 代码片段）
6. embed_sve_code()         → 按行号降序替换（避免行偏移）
7. inject_headers()         → 注入 <arm_sve.h> 和 <stdint.h>
8. 写出文件                                            [可选] 复制到 stage4_sve.c
```

#### 阶段文件（`--save-stages`）

使用 `--save-stages` 后，每次运行在 `stages/` 目录下保存 4 个文件：

| 文件 | 内容 |
|------|------|
| `<name>_stage1_loops.txt` | 每个循环的变量、数组读写、操作符、类型上下文、嵌套层级 |
| `<name>_stage2_analyzed.txt` | 模式、数据类型、可向量化状态、SVE 类型后缀、拒绝原因 |
| `<name>_stage3_unroll.txt` | 展开因子、预取开关/距离、决策原因 |
| `<name>_stage4_sve.c` | 最终 SVE 代码（与 `-o` 输出内容相同） |

#### 代码嵌入策略

```
按 node_coord_start 降序排列替换任务
（从文件末尾往前替换，避免行号偏移问题）

替换 analyze_loops() 选中的最内层循环：
  source_lines[start:end] ← [原行号注释行 + SVE 代码片段]
```

#### 头文件注入

在文件首个 `#include` 之前插入所需头文件（若已存在则跳过）：
```c
#include <arm_sve.h>   /* SVE intrinsics */
#include <stdint.h>    /* int64_t 类型 */
```

---

## 4. 支持的向量化模式

仅处理最内层 for 循环，循环步长必须为常量 1。

---

### 逐元素运算（ELEMENTWISE）— 三段式展开

#### 识别条件
- 有数组写操作
- 写下标只含当前循环变量（无多余变量）
- 无归约变量，无条件分支

#### 生成结构（unroll=2，开启预取）

```c
/* 输入 */
for (int i = 0; i < n; i++)
    a[i] = b[i] + c[i];

/* 输出 */
int64_t vl  = svcntw();
int64_t i = 0;
svbool_t pg_all = svptrue_b32();   /* 全真谓词，主/清理循环复用 */

/* 主循环：2×vl 元素/迭代，svptrue 无谓词计算开销 */
for (; i + 2*vl <= (int64_t)n; i += 2*vl) {
    svprfd(pg_all, &b[i + 8*vl], SV_PLDL1KEEP);  /* 软件预取 */
    svprfd(pg_all, &c[i + 8*vl], SV_PLDL1KEEP);
    svfloat32_t vb_0 = svld1_f32(pg_all, &b[i]);
    svfloat32_t vc_0 = svld1_f32(pg_all, &c[i]);
    svfloat32_t va_0 = svadd_f32_x(pg_all, vb_0, vc_0);
    svst1_f32(pg_all, &a[i], va_0);
    svfloat32_t vb_1 = svld1_f32(pg_all, &b[i + 1*vl]);
    svfloat32_t vc_1 = svld1_f32(pg_all, &c[i + 1*vl]);
    svfloat32_t va_1 = svadd_f32_x(pg_all, vb_1, vc_1);
    svst1_f32(pg_all, &a[i + 1*vl], va_1);
}
/* 清理循环：处理展开余量（最多 1 次），仍用 svptrue */
for (; i + vl <= (int64_t)n; i += vl) { ... }
/* 尾部：svwhilelt 最多执行 1 次 */
if (i < (int64_t)n) {
    svbool_t pg = svwhilelt_b32(i, (int64_t)n);
    ...
}
```

支持操作：`+`（svadd）、`-`（svsub）、`*`（svmul）、`/`（svdiv）、纯赋值拷贝

#### 已支持场景 / 当前缺陷

| 场景 | 状态 |
|------|------|
| `a[i] = b[i] + c[i]`（双源 elementwise） | ✅ 支持，unroll=2 + svprfd |
| `a[i] = b[i]`（纯拷贝） | ✅ 支持，unroll=2，不预取 |
| `a[i] += b[i]`（原地更新，写=读同名） | ✅ 支持，额外加载 `a[i]` 旧值 |
| `a[i] = b[i] + c[i] * d[i]`（多操作） | ⚠️ 只处理前两个读数组，第三个丢失 |
| `{ tmp[i] = ...; a[i] = tmp[i] * 2; }`（多语句） | ⚠️ 只处理单赋值语句 |
| `a[i+k] = b[i]`（写有常量偏移） | ✅ 安全：若 k>0 则检测为 RAW 依赖，拒绝向量化 |
| 步长 ≠ 1 | ✅ 安全：检测后拒绝向量化 |

---

### 归约运算（REDUCTION）

#### 识别条件
- 存在归约变量（标量）
- 循环体为 `scalar += a[i]` 或 `scalar = scalar + a[i]` 形式

#### 生成结构

```c
/* 输入 */
float sum = 0.0f;
for (int i = 0; i < n; i++)
    sum += a[i];

/* 输出 */
svfloat32_t vred = svdup_f32(0.0f);   /* 向量累加器 */
int64_t i = 0;
int64_t sve_vl = svcntw();
svbool_t pg;
for (; i < (int64_t)n; i += sve_vl) {
    pg = svwhilelt_b32(i, (int64_t)n);
    svfloat32_t va = svld1_f32(pg, &a[i]);
    vred = svadd_f32_m(pg, vred, va);   /* merge: 非活跃通道保持 vred */
}
sum = svaddv_f32(svptrue_b32(), vred);  /* 水平归约到标量 */
```

#### 已支持场景 / 当前缺陷

| 场景 | 状态 |
|------|------|
| `sum += a[i]`（加法归约） | ✅ 支持，`svaddv_f32` 水平归约 |
| float / double / int 三种类型 | ✅ 支持 |
| `max = a[i] > max ? a[i] : max`（最大值归约） | ⚠️ 未支持，需 `svmaxv_f32` |
| `prod *= a[i]`（乘积归约） | ⚠️ 未支持 |
| 归约初始值非 0（`float sum = 1.0f`） | ⚠️ 保守生成 `svdup_f32(0.0f)`，初始值不准确 |
| 多累加器展开（打破顺序依赖） | ⚠️ 未实现，当前 unroll 固定为 1 |
| 归约用 `svptrue_b32()` 做最终 `svaddv` | ⚠️ 正确性问题：应使用最后一次迭代的 `pg`，而非全真谓词（尾部 padding 值为 0，加法归约正确；但通用性受限） |

---

### 条件循环（CONDITIONAL）

#### 识别条件
- 循环体顶层为 `if` 语句（`has_condition == True`）
- `condition_info` 能提取出比较符和被比较的数组/变量

#### 生成结构

```c
/* 输入（无 else） */
for (int i = 0; i < n; i++)
    if (a[i] > 0.0f) b[i] = a[i];

/* 输出 */
int64_t i = 0;
int64_t sve_vl = svcntw();
svbool_t pg, cond_pg;
for (; i < (int64_t)n; i += sve_vl) {
    pg = svwhilelt_b32(i, (int64_t)n);
    svfloat32_t va = svld1_f32(pg, &a[i]);
    cond_pg = svcmpgt_f32(pg, va, svdup_f32(0.0f));  /* if(a[i]>0) */
    svst1_f32(cond_pg, &b[i], va);   /* 只写 cond_pg=true 的通道 */
}

/* 输入（有 else，赋 0） */
for (int i = 0; i < n; i++)
    if (a[i] > 0.0f) b[i] = a[i]; else b[i] = 0.0f;

/* 输出（else 分支用 svsel 合并） */
    svfloat32_t vresult = svsel_f32(cond_pg, va, svdup_f32(0));
    svst1_f32(pg, &b[i], vresult);
```

#### 已支持场景 / 当前缺陷

| 场景 | 状态 |
|------|------|
| `if (a[i] > 0) b[i] = a[i]`（无 else） | ✅ `svcmpgt` + `svst1(cond_pg, ...)` |
| `if (a[i] > threshold)` （标量右值） | ✅ 标量广播为 `svdup_f32(threshold)` |
| `if ... else b[i] = 0`（else 赋 0） | ✅ `svsel` + `svdup_f32(0)` |
| `if (a[i] > 0) b[i] = c[i] + d[i]`（分支内含运算） | ⚠️ 运算丢失，只做简单赋值 |
| `if ... else b[i] = val`（else 赋非 0 值） | ⚠️ else 值固定为 0，不提取实际 else 赋值 |
| `if (a[i] > 0) { b[i] = ...; c[i] = ...; }`（多写） | ⚠️ 只处理第一个写操作 |
| `if (i % 2 == 0)`（非数组条件） | ⚠️ `condition_var` 为空，代码可能不正确 |
| 条件修改循环变量 `i` | ✅ 安全：检测后标为 NOT_VECTORIZABLE |
| 无展开 / 无预取 | ℹ️ 单段式，每次迭代都用 `svwhilelt`（待扩展） |

---

### 预留扩展：矩阵向量化（未来版本）

当前版本中，矩阵专用识别与代码生成模块已下线，默认行为是**保守跳过矩阵类嵌套循环并保留原始代码**。

为支持后续逐步扩展，当前代码结构仍保留了以下可扩展点：

- `LoopAnalyzer`：可在模式分类阶段恢复矩阵识别规则；
- `LoopUnroller`：可新增矩阵场景的展开/预取策略；
- `SVECodeGen`：可通过独立分发分支接入矩阵模板生成器；
- `tests/test_matrix.c`：已保留矩阵回归样例，可用于“跳过 → 部分支持 → 完整支持”的阶段验收。

建议恢复顺序：

1. 先增强依赖判定与嵌套循环分析（保证正确性）；
2. 再恢复矩阵模式识别（仅识别，不生成）；
3. 最后接入矩阵代码生成与性能策略（展开、预取、循环交换）。

---

### 未识别（UNKNOWN / NOT_VECTORIZABLE）

以下情况会被跳过，保留原始代码并附加注释说明原因：

| 原因 | 说明 |
|------|------|
| 步长未知或非 1 | `loop_step == 0` 或 `loop_step != 1` |
| RAW 依赖 | 写 `a[i]`，同时读 `a[i+k]`（k>0） |
| WAR 别名依赖 | 写数组在读列表中出现，且有常量偏移 |
| 条件分支修改 `i` | 控制流依赖，无法静态向量化 |
| 模式为 UNKNOWN | 不满足任何已知模式（如间接寻址 `a[idx[i]]`） |
| 数据类型未知 | `type_context` 中无法推断元素类型（降级为 float32） |

---

## 5. ARM SVE Intrinsics 映射表

### 加载 / 存储

| 操作 | float32 | float64 | int32 | int64 |
|------|---------|---------|-------|-------|
| 连续加载 | `svld1_f32` | `svld1_f64` | `svld1_s32` | `svld1_s64` |
| 连续存储 | `svst1_f32` | `svst1_f64` | `svst1_s32` | `svst1_s64` |

### 算术运算

| 操作 | float32 | float64 | int32 |
|------|---------|---------|-------|
| 加（_x） | `svadd_f32_x` | `svadd_f64_x` | `svadd_s32_x` |
| 加（_m） | `svadd_f32_m` | `svadd_f64_m` | `svadd_s32_m` |
| 减（_x） | `svsub_f32_x` | `svsub_f64_x` | `svsub_s32_x` |
| 乘（_x） | `svmul_f32_x` | `svmul_f64_x` | `svmul_s32_x` |
| 除（_x） | `svdiv_f32_x` | `svdiv_f64_x` | `svdiv_s32_x` |
| 乘加 FMA（_x） | `svmla_f32_x` | `svmla_f64_x` | `svmla_s32_x` |

### 归约

| 操作 | float32 | float64 | int32 |
|------|---------|---------|-------|
| 水平求和 | `svaddv_f32` | `svaddv_f64` | `svaddv_s32` |
| 水平最大 | `svmaxv_f32` | `svmaxv_f64` | — |
| 水平最小 | `svminv_f32` | `svminv_f64` | — |

> `svmaxv` / `svminv` 已在映射表中定义，但当前归约模式仅支持 `+=` 求和，最大/最小值归约待实现（见[第 6 节](#6-未实现功能待扩展)）。

### 比较谓词

| 比较符 | float32 | float64 | int32 |
|--------|---------|---------|-------|
| `>` | `svcmpgt_f32` | `svcmpgt_f64` | `svcmpgt_s32` |
| `<` | `svcmplt_f32` | `svcmplt_f64` | `svcmplt_s32` |
| `>=` | `svcmpge_f32` | `svcmpge_f64` | `svcmpge_s32` |
| `<=` | `svcmple_f32` | `svcmple_f64` | `svcmple_s32` |
| `==` | `svcmpeq_f32` | `svcmpeq_f64` | `svcmpeq_s32` |
| `!=` | `svcmpne_f32` | `svcmpne_f64` | `svcmpne_s32` |

### 其他常用

| 操作 | 函数 | 说明 |
|------|------|------|
| 循环谓词（32位） | `svwhilelt_b32(i, n)` | 生成 `i < n` 对应谓词 |
| 循环谓词（64位） | `svwhilelt_b64(i, n)` | double / int64 使用 |
| 全真谓词（32位） | `svptrue_b32()` | 所有通道为真，主循环复用 |
| 全真谓词（64位） | `svptrue_b64()` | double / int64 使用 |
| 标量广播 | `svdup_f32(x)` | 将标量 x 广播到所有通道 |
| 条件选择 | `svsel_f32(pg, a, b)` | pg 为真选 a，否则选 b |
| 软件预取 | `svprfd(pg, ptr, hint)` | 预取到 L1，hint 用 `SV_PLDL1KEEP` |
| 向量长度（float32） | `svcntw()` | 运行时每个向量中的 float32 元素数 |
| 向量长度（double） | `svcntd()` | 运行时每个向量中的 double 元素数 |

---

## 6. 未实现功能（待扩展）

### 5.1 更多归约操作

**当前**：只支持 `+=` 求和归约（`svaddv`）。

**待实现**：
- `max` 归约：`if(a[i] > max) max = a[i]` → `svmaxv_f32`
- `min` 归约：`if(a[i] < min) min = a[i]` → `svminv_f32`
- `*=` 乘积归约

**涉及文件**：`LoopAnalyzer._check_reduction()`、`SVECodeGen._gen_reduction()`、`SVECodeGen._REDUCE` 映射表

### 5.2 非连续内存访问（Gather / Scatter）

**当前**：只支持连续内存访问（`svld1` / `svst1`）。

**待实现**：
- **Gather 加载**：`svld1_gather_s32offset_f32`——处理 `a[idx[i]]` 间接寻址
- **Scatter 存储**：`svst1_scatter_s32offset_f32`——处理间接写操作

**涉及文件**：`CLoopExtraction._parse_array_ref()`、`SVECodeGen`（新增 gather/scatter 模板）

### 5.3 多语句逐元素循环体

**当前**：ELEMENTWISE 模式仅处理循环体含**单个**赋值语句的情况。

**待实现**：
```c
for (int i = 0; i < n; i++) {
    tmp[i] = a[i] + b[i];   /* 语句 1 */
    c[i]   = tmp[i] * 2.0f; /* 语句 2，依赖语句 1 */
}
```
需按语句间依赖顺序生成多条 SVE 运算链。

**涉及文件**：`CLoopExtraction._BodyAnalyzer`（收集多个写操作）、`SVECodeGen._gen_elementwise()`

### 5.4 `while` 循环支持

**当前**：只处理 `for` 循环（`c_ast.For`）。

**待实现**：在 `LoopExtractor` 中添加 `visit_While()` 方法，将 while 循环转换为等价 for 语义后进行后续分析。

**涉及文件**：`CLoopExtraction.LoopExtractor`

### 5.5 非单位步长的向量化

**当前**：步长不为 1 直接标记为 `NOT_VECTORIZABLE`。

**待实现**：步长为常量 2/4 时，利用 `svld2`（交错加载）实现向量化：
```c
for (int i = 0; i < n; i += 2)   /* 步长 2 → svld2_f32 */
    a[i] = b[i] * scale;
```

**涉及文件**：`LoopAnalyzer._check_vectorizability()`、`SVECodeGen`（新增步长模板）

### 5.6 归约初始值精确提取

**当前**：归约初始值保守推断为 `0.0f` / `0.0` / `0`。

**待实现**：从声明语句（`float sum = init_val;`）提取实际初始值，生成精确的 `svdup_f32(init_val)` 广播。

**涉及文件**：`LoopAnalyzer._infer_reduction_init()`、`SVECodeGen._gen_reduction()`

### 5.7 REDUCTION 的展开支持

**当前**：`LoopUnroller` 对 REDUCTION 固定 `unroll_factor=1`，`_gen_reduction` 接收 `cfg` 但不使用。

**待实现**：使用多个独立向量累加器（`vred_0`/`vred_1`）并行累加，循环后合并，突破顺序依赖瓶颈：
```c
svfloat32_t vred_0 = svdup_f32(0.0f);
svfloat32_t vred_1 = svdup_f32(0.0f);
for (; i + 2*vl <= n; i += 2*vl) {
    vred_0 = svadd_f32_m(pg, vred_0, svld1_f32(pg, &a[i]));
    vred_1 = svadd_f32_m(pg, vred_1, svld1_f32(pg, &a[i+vl]));
}
vred_0 = svadd_f32_x(pg_all, vred_0, vred_1);  /* 合并 */
sum = svaddv_f32(svptrue_b32(), vred_0);
```

**涉及文件**：`SVECodeGen._gen_reduction()`、`LoopUnroller._decide()`

### 5.8 数据对齐优化

**当前**：使用通用非对齐加载 `svld1`（SVE 支持非对齐，但对齐访问性能更优）。

**待实现**：检测数组对齐属性（`__attribute__((aligned(N)))`），生成带对齐提示的加载路径。

---

## 7. 使用示例

### 基本向量化

```bash
python SVEVectorizer.py my_kernel.c -o my_kernel_sve.c
```

### 无系统头文件的简单 C 文件

```bash
python SVEVectorizer.py simple.c --no-cpp -o simple_sve.c
```

### 查看分析报告（不写文件）

```bash
python SVEVectorizer.py my_kernel.c --no-cpp --report --dry-run
```

### 详细显示分析过程和展开决策

```bash
python SVEVectorizer.py my_kernel.c --no-cpp --verbose --report
```

### 强制指定展开因子和预取距离

```bash
# 展开 4 份，预取距离 16*vl
python SVEVectorizer.py my_kernel.c --no-cpp --unroll 4 --prefetch-dist 16

# 关闭展开（退化为双段：svptrue主循环 + svwhilelt尾部）
python SVEVectorizer.py my_kernel.c --no-cpp --unroll 1
```

### 保存各阶段中间结果

```bash
python SVEVectorizer.py my_kernel.c --no-cpp --save-stages
# 生成以下文件：
#   stages/my_kernel_stage1_loops.txt      循环提取结果
#   stages/my_kernel_stage2_analyzed.txt   分析结果
#   stages/my_kernel_stage3_unroll.txt     展开决策
#   stages/my_kernel_stage4_sve.c          最终 SVE 代码
```

### 对含系统头文件的 C 文件（如 TSVC benchmark）

需先用 gcc 预处理并展开宏，再用 `--no-cpp` 解析：

```bash
# 第一步：预处理（使用 fake_libc_include 替代系统头文件）
gcc -E -nostdinc \
    -I fake_libc_include \
    -I TSVC_2/src \
    "-D__attribute__(x)=" "-D__extension__=" \
    "-D__restrict=" "-D__restrict__=" "-D__inline=inline" \
    TSVC_2/src/tsvc.c -o TSVC_2/tsvc_preprocessed_raw.c

# 第二步：去除 # 行标记
python -c "
lines = [l for l in open('TSVC_2/tsvc_preprocessed_raw.c') if not l.startswith('#')]
open('TSVC_2/tsvc_preprocessed.c','w').writelines(lines)"

# 第三步：向量化
python SVEVectorizer.py TSVC_2/tsvc_preprocessed.c \
    --no-cpp -o TSVC_2/tsvc_preprocessed_sve.c --save-stages
```

### 编译生成的 SVE 代码（需 ARM 工具链）

```bash
# 语法检查
aarch64-linux-gnu-gcc -fsyntax-only -march=armv8-a+sve my_kernel_sve.c

# 编译为可执行文件
aarch64-linux-gnu-gcc -O2 -march=armv8-a+sve my_kernel_sve.c -o my_kernel_sve
```

---

## 8. 测试

### 运行测试套件

```bash
cd /path/to/SIMD
python tests/run_tests.py
```

共 **32 个测试**，覆盖 ELEMENTWISE/REDUCTION/CONDITIONAL、矩阵保守跳过行为、头文件注入、dry-run 模式和报告输出。

### 测试文件说明

| 文件 | 测试内容 | 验证的关键 intrinsics |
|------|---------|----------------------|
| `tests/test_elementwise.c` | float 加、double 乘、int 减、float 除 各一个循环 | `svld1_f32`、`svadd_f32_x`、`svst1_f32`、`svwhilelt_b32` |
| `tests/test_reduction.c` | float / int / double 数组求和归约各一个 | `svaddv_f32`、`svdup_f32`、`svadd_f32_m` |
| `tests/test_conditional.c` | ReLU（if/else）/ 单分支阈值 / 负值清零 | `svcmpgt_f32`、`svcmplt_f32`、`cond_pg` |
| `tests/test_matrix.c` | 3 层嵌套矩阵乘法、矩阵向量乘 | `SVE-VECTORIZER: SKIPPED`（保守跳过） |

### 查看单个文件的输出

```bash
python SVEVectorizer.py tests/test_elementwise.c --no-cpp --report
cat tests/test_elementwise_sve.c
```

### TSVC Benchmark 端到端测试

使用 [TSVC_2](https://github.com/llvm/llvm-test-suite/tree/main/MultiSource/Benchmarks/TSVC) 作为真实场景验证。TSVC 包含 138 个测试函数，共 154 个顶层循环。

```bash
# innermost（默认）：端到端生成输出 + 保存阶段文件
python SVEVectorizer.py TSVC_2/tsvc_preprocessed.c \
    --no-cpp -o TSVC_2/tsvc_preprocessed_sve.c --save-stages --report
```

**当前结果（2026-03-02）**：

| 模式 | 发现循环总数 | 成功向量化 | 跳过 |
|------|-------------:|-----------:|-----:|
| `innermost`（默认） | 156 | **102** | 54 |

> 注：`innermost` 显著提升了可向量化循环数量；但对矩阵/复杂嵌套模式，当前识别与代码生成仍在完善中。

> 最新完整明细（成功向量化函数名、失败函数名与失败原因统计）见：
> [TSVC_2/tsvc_vectorization_report_2026-03-02.md](TSVC_2/tsvc_vectorization_report_2026-03-02.md)

**`innermost` 模式成功向量化示例（节选）**：

| 行号（预处理后） | 模式 | 对应 TSVC 函数 | 说明 |
|----------------|------|--------------|------|
| 306 | ELEMENTWISE | 线性循环 | `svld1 + svadd + svst1` |
| 411 | ELEMENTWISE | 线性循环 | `svld1 + svadd + svst1` |
| 677 | CONDITIONAL | 条件循环 | `svcmp + svst1(cond_pg)` |
| 948 | ELEMENTWISE | 乘法循环 | `svmul` |
| 1182 | ELEMENTWISE | int32 循环 | `svld1_s32 + svadd_s32 + svst1_s32` |

---

#### TSVC 向量化失败原因分析

在 `innermost` 模式下，TSVC 中有 54 个循环被跳过。当前主要失败来源如下（按循环计数）：

| 失败原因（摘要） | 数量 |
|---|---:|
| 模式识别为 `UNKNOWN`（提示“无循环携带依赖，可向量化”但未命中已支持模板） | 19 |
| 别名依赖（`aa` / `a` / `b` / `bb` / `c` / `e`） | 18 |
| 非单位步长（`step=5/-1/2`） | 7 |
| 步长未知（非常量） | 3 |
| 循环携带 RAW 依赖 | 3 |
| 其他偏移依赖 | 4 |

失败函数名与主要失败原因、成功函数名完整列表已移到独立报告：

- [TSVC_2/tsvc_vectorization_report_2026-03-02.md](TSVC_2/tsvc_vectorization_report_2026-03-02.md)

该报告包含：

- 成功向量化函数名（完整列表）
- 失败函数名与主要失败原因（完整表）
- 失败原因统计（完整表）
