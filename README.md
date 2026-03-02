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
│  analyze_loops()    │  输出: List[AnalyzedLoop]
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
| `SVECodeGen.py` | ✅ 已实现 | 四种模式的 SVE intrinsics 代码生成（三段式） |
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

只分析 `nesting_level == 0` 的顶层循环；矩阵模式的内层循环由 `SVECodeGen` 通过 `inner_loops` 处理。

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
| 1 | `MATRIX` | 顶层且含 `inner_loops`，最深层循环的写/读下标含外层循环变量 |
| 2 | `REDUCTION` | `is_reduction == True`，存在归约变量 |
| 3 | `CONDITIONAL` | `has_condition == True`，循环体主体为 `if` 语句 |
| 4 | `ELEMENTWISE` | 写数组下标只含当前循环变量，无循环携带依赖 |
| 5 | `UNKNOWN` | 以上均不满足 |

#### 可向量化性检查规则

| 规则 | 触发条件 | 结果 |
|------|---------|------|
| 非单位步长 | `loop_step != 1` 或 `loop_step == 0` | NOT_VECTORIZABLE |
| RAW 依赖 | 写 `a[i]` 同时读 `a[i+k]`（k>0） | NOT_VECTORIZABLE |
| 别名偏移依赖 | 写数组出现在读数组中且有常量偏移 | NOT_VECTORIZABLE |
| 条件控制依赖 | 条件分支内修改循环控制变量 | NOT_VECTORIZABLE |
| 矩阵模式 | j 层可向量化，k 层保持标量 | VECTORIZABLE |

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
| MATRIX | 任意 | **2** | **开启** | 内层 j 循环展开 |
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
| MATRIX | `svdup` + `svwhilelt` + `svld1` + `svmla_x` + `svst1` | — |

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

只替换顶层循环（nesting_level == 0）：
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

各模式均只处理 `nesting_level == 0` 的**顶层 for 循环**，循环步长必须为常量 1。

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

### 矩阵运算（MATRIX）

#### 识别条件
- 顶层循环含内层循环（`nesting_level=0` 且 `inner_loops` 非空）
- 最深层循环的写或读下标中含外层循环变量

#### 生成结构（向量化最内层 j 循环）

```c
/* 输入（3 层嵌套矩阵乘法） */
for (int i = 0; i < M; i++)
    for (int k = 0; k < K; k++)
        for (int j = 0; j < N; j++)
            C[i*N+j] += A[i*K+k] * B[k*N+j];

/* 输出（i/k 保持标量，j 向量化） */
int64_t sve_vl = svcntw();
svbool_t pg;
for (int i = 0; i < M; i++) {
    for (int k = 0; k < K; k++) {
        svfloat32_t va = svdup_f32(A[i*K+k]);   /* 广播标量 A[i*K+k] */
        int64_t j = 0;
        for (; j < (int64_t)N; j += sve_vl) {
            pg = svwhilelt_b32(j, (int64_t)N);
            svfloat32_t vb = svld1_f32(pg, &B[k*N+j]);
            svfloat32_t vc = svld1_f32(pg, &C[i*N+j]);
            vc = svmla_f32_x(pg, vc, va, vb);   /* vc += va * vb（FMA） */
            svst1_f32(pg, &C[i*N+j], vc);
        }
    }
}
```

#### 已支持场景 / 当前缺陷

| 场景 | 状态 |
|------|------|
| 3 层嵌套 `C[i*N+j] += A[i*K+k] * B[k*N+j]` | ✅ `svmla_f32_x` FMA 向量化 j 循环 |
| 2 层嵌套矩阵向量乘 `y[i] += A[i*N+j] * x[j]` | ✅ 向量化内层 j 循环 |
| `a[i][j]` 二维数组写法（vs 平铺 `a[i*N+j]`） | ✅ 两种下标模式均能识别 |
| 4 层以上嵌套 | ⚠️ 只取最深一层，中间层保持标量；超过 3 层可能识别错误 |
| 非方阵 / 非标准 i-k-j 循序 | ⚠️ A/B/C 识别依赖下标变量启发式，非 i-k-j 顺序可能错配 |
| j 循环展开 / 预取 | ⚠️ 未实现，固定单段式 |
| `C[i][j] += ...`（写为二维数组下标） | ✅ 支持，`_parse_subscript` 能提取 `i`、`j` |
| 内层循环含 `if` | ⚠️ 内层条件分支不处理 |

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

共 **32 个测试**，覆盖四种向量化模式、头文件注入、dry-run 模式和报告输出，全部通过。

### 测试文件说明

| 文件 | 测试内容 | 验证的关键 intrinsics |
|------|---------|----------------------|
| `tests/test_elementwise.c` | float 加、double 乘、int 减、float 除 各一个循环 | `svld1_f32`、`svadd_f32_x`、`svst1_f32`、`svwhilelt_b32` |
| `tests/test_reduction.c` | float / int / double 数组求和归约各一个 | `svaddv_f32`、`svdup_f32`、`svadd_f32_m` |
| `tests/test_conditional.c` | ReLU（if/else）/ 单分支阈值 / 负值清零 | `svcmpgt_f32`、`svcmplt_f32`、`cond_pg` |
| `tests/test_matrix.c` | 3 层嵌套矩阵乘法、矩阵向量乘 | `svmla_f32_x`、`svdup_f32`、`j-loop vectorized` |

### 查看单个文件的输出

```bash
python SVEVectorizer.py tests/test_elementwise.c --no-cpp --report
cat tests/test_elementwise_sve.c
```

### TSVC Benchmark 端到端测试

使用 [TSVC_2](https://github.com/llvm/llvm-test-suite/tree/main/MultiSource/Benchmarks/TSVC) 作为真实场景验证。TSVC 包含 138 个测试函数，共 154 个顶层循环。

```bash
# 直接运行（tsvc_preprocessed.c 已预置在 TSVC_2/ 目录下）
python SVEVectorizer.py TSVC_2/tsvc_preprocessed.c \
    --no-cpp -o TSVC_2/tsvc_preprocessed_sve.c --save-stages --report
```

**当前结果**（仅处理顶层循环）：

| 指标 | 数量 |
|------|------|
| 发现顶层循环 | 154 |
| 成功向量化 | **5** |
| 跳过 | 149 |

**成功向量化的循环（5 个）**：

| 行号（预处理后） | 模式 | 对应 TSVC 函数 | 说明 |
|----------------|------|--------------|------|
| 986 | ELEMENTWISE ADD float | `s000` | `a[i] = b[i] + 1`，unroll=2 + svprfd 预取 |
| 1155 | CONDITIONAL | 条件分支函数 | 条件赋值，`svcmpgt` 谓词保护写 |
| 2811 | REDUCTION ADD float | 归约函数 | 数组求和，`svaddv_f32` 水平归约 |
| 2838 | REDUCTION ADD float | 归约函数 | 数组求和，`svaddv_f32` 水平归约 |
| 2951 | ELEMENTWISE ASSIGN float | 赋值函数 | 纯拷贝，`svld1 + svst1` |

---

#### TSVC 向量化失败原因分析

TSVC 中 149 个循环跳过，失败原因分为以下六类：

---

**原因一：计时外层循环屏蔽（最主要原因，影响全部 138 个函数）**

TSVC 每个测试函数的结构如下：

```c
for (int nl = 0; nl < 2*iterations; nl++) {   // 计时重复循环（顶层）
    for (int i = 0; i < LEN_1D; i++) {         // 真正的向量化目标（内层）
        a[i] = b[i] + c[i];
    }
    dummy(...);
}
```

工具只处理 `nesting_level == 0` 的**顶层**循环。`nl` 循环是顶层，但它的循环体含有函数调用（`dummy(...)`），提取不到数组读写信息（`array_reads/array_writes` 均为空），被识别为 `UNKNOWN` 模式后跳过。
真正需要向量化的 `i` 内层循环位于 `nesting_level = 1`，被工具完全忽略。

---

**原因二：RAW 循环携带依赖（影响依赖测试类循环）**

写操作依赖前一次迭代的读结果，存在循环携带依赖，无法并行执行：

```c
// s111：a[i] 依赖 a[i-1]
for (int i = 1; i < LEN_1D; i += 2)
    a[i] = a[i - 1] + b[i];

// s112：反向循环，a[i+1] 依赖 a[i]
for (int i = LEN_1D - 2; i >= 0; i--)
    a[i+1] = a[i] + b[i];
```

工具检测到 `写 a[i]` 同时 `读 a[i+offset]`（offset ≠ 0）后，标为 `NOT_VECTORIZABLE`，拒绝向量化。这是**正确的保守行为**。

---

**原因三：非单位步长（影响步长测试类循环）**

循环步长不为 1，当前工具不支持：

```c
// s111：步长 2
for (int i = 1; i < LEN_1D; i += 2)
    a[i] = a[i - 1] + b[i];

// s131：步长 5，手动展开
for (int i = 0; i < LEN_1D - 5; i += 5) {
    a[i]   = a[i+1] * a[i];
    a[i+1] = a[i+2] * a[i+1];
    ...
}
```

向量化非单位步长需要 `svld2`（交错加载）等特殊 intrinsics，目前未实现（见 §6.5）。

---

**原因四：多语句循环体（标量展开类循环）**

循环体含多条赋值语句，或使用了局部标量中间变量：

```c
// s125：标量展开，两条语句
real_t s;
for (int i = 0; i < LEN_1D; i++) {
    s    = b[i] + c[i] * d[i];   // 语句1：标量 s
    a[i] = s * s;                  // 语句2：依赖 s
}
```

工具的 `_BodyAnalyzer` 只处理第一条赋值语句，第二条被丢弃，导致写数组 `a` 和读数组 `b/c/d` 的关系提取不完整，识别为 `UNKNOWN`。

---

**原因五：调用数学函数（函数调用类循环）**

循环体内含 `sinf`、`cosf` 等数学函数调用：

```c
// s4112：调用 sinf/cosf
for (int i = 0; i < LEN_1D; i++)
    a[i] = sinf(b[i]) + cosf(c[i]);
```

pycparser AST 中，`sinf(b[i])` 被解析为 `FuncCall` 节点，而非 `BinaryOp`。`_BodyAnalyzer` 不处理 `FuncCall`，无法提取操作符和读数组，最终识别为 `UNKNOWN`。实际上此类循环完全可以向量化（SVE 有 `svsin`/`svcos`）。

---

**原因六：含 `break`/控制流中断的循环**

循环体内有提前退出语句：

```c
// 含 break 的条件退出
for (int i = 0; i < LEN_1D; i++) {
    if (c[i] > b[i]) break;
    a[i] = b[i] * c[i];
}
```

存在控制流依赖，无法静态向量化。工具当前不识别 `break`，会将其当作普通 `if` 处理，可能生成错误代码。

---

**各类失败原因汇总**：

| 类别 | 典型 TSVC 函数 | 根本原因 | 工具当前行为 |
|------|--------------|---------|------------|
| 计时外层循环屏蔽 | 全部 138 个函数 | 只处理顶层循环，内层被忽略 | UNKNOWN，跳过 |
| RAW 循环携带依赖 | `s111`、`s112`、`s121` 等 | 写依赖前一迭代读 | 正确检测，拒绝 |
| 非单位步长 | `s111`、`s131` 等 | 步长 ≠ 1 | 正确检测，拒绝 |
| 多语句 / 标量展开 | `s125`、`s126` 等 | 只提取第一条赋值 | UNKNOWN，跳过 |
| 数学函数调用 | `s4112` 等 | FuncCall 节点未处理 | UNKNOWN，跳过 |
| `break` / 控制流中断 | `s431` 等 | 不识别 break | 潜在错误处理 |

---

#### 逐函数失败原因列表

> **符号说明**
> ✅ = 成功向量化  |  🔒 = 外层 nl 计时循环屏蔽（内层 i 循环位于 nesting_level=1，工具只处理顶层）
> 🔗 = RAW 循环携带依赖  |  📐 = 非单位步长  |  📝 = 多语句/标量临时变量依赖
> 🔣 = 间接寻址（gather/scatter）  |  📐🔢 = 非线性下标  |  🔀 = goto/break 控制流
> 📞 = 函数调用/数学函数  |  🔲 = 二维数组 / 非连续访问  |  ❓ = 其他

| 函数名 | TSVC 类别描述 | 内层循环摘要 | 失败原因 |
|--------|-------------|------------|---------|
| **s000** | 线性依赖测试 | `a[i] = b[i] + 1` | ✅ **成功**（唯一 1D 简单 ELEMENTWISE） |
| **s111** | 线性依赖测试 | `a[i] = a[i-1] + b[i]`，步长 2 | 🔒+🔗+📐 外层屏蔽；若可见则因 `a[i]←a[i-1]` 具有 RAW 依赖且步长 ≠1 |
| **s1111** | 跳跃数据访问 | `a[2*i] = ...`（步长 2） | 🔒+📐 外层屏蔽；若可见则因 `a[2*i]` 非单位步长 |
| **s112** | 线性依赖测试 / 逆序循环 | `a[i+1] = a[i] + b[i]`，i 递减 | 🔒+🔗 外层屏蔽；若可见则因 `a[i+1]←a[i]` 具有 RAW 依赖 |
| **s1112** | 线性依赖测试 / 逆序循环 | `a[i] = b[i] + 1.0`，i 递减 | 🔒 外层屏蔽；若可见则因步长为 -1（递减循环） |
| **s113** | `a(i)=a(1)` 无真实依赖 | `a[i] = a[0] + b[i]` | 🔒 外层屏蔽；若可见则因 `a[0]` 为常量引用（工具无法证明无别名）可能拒绝 |
| **s1113** | 一次迭代依赖 | `a[i] = a[LEN_1D/2] + b[i]` | 🔒 外层屏蔽；若可见则因 `a[LEN_1D/2]` 不确定是否与写重叠 |
| **s114** | 转置向量化 / 跳跃访问 | `aa[i][j] = aa[j][i] + bb[i][j]`（三角） | 🔒+🔲 外层屏蔽；若可见则因 `aa[j][i]` 非顺序行访问（转置读） |
| **s115** | 三角 saxpy | `a[i] -= aa[j][i] * a[j]`，j 为外层变量 | 🔒+🔗 外层屏蔽；若可见则因 `a[i]` 与 `a[j]` 存在潜在别名依赖 |
| **s1115** | 三角 saxpy | `aa[i][j] = aa[i][j]*cc[j][i] + bb[i][j]` | 🔒+🔲 外层屏蔽；若可见则因 `cc[j][i]` 非行连续访问 |
| **s116** | 线性依赖测试 | `a[i] = a[i+1]*a[i]`，步长 5，多语句 | 🔒+📝+📐 外层屏蔽；若可见则因步长 5 + 循环体含多条 `a[i+k]` 自依赖赋值 |
| **s118** | 潜在点积递归 | `a[i] += bb[j][i] * a[i-j-1]` | 🔒+🔗 外层屏蔽；若可见则因 `a[i]` 读 `a[i-j-1]`（j 可变），RAW 依赖 |
| **s119** | 线性依赖测试 | `aa[i][j] = aa[i-1][j-1] + bb[i][j]` | 🔒+🔗 外层屏蔽；若可见则因 `aa[i][j]←aa[i-1][j-1]` 跨行 RAW 依赖 |
| **s1119** | 线性依赖测试 | `aa[i][j] = aa[i-1][j] + bb[i][j]` | 🔒+🔗 外层屏蔽；若可见则因 `aa[i][j]←aa[i-1][j]` 跨行 RAW 依赖 |
| **s121** | 归纳变量识别 | `j=i+1; a[i]=a[j]+b[i]` | 🔒+🔗 外层屏蔽；若可见则因 `a[i]` 读 `a[i+1]`（反向依赖，工具检测为 RAW） |
| **s122** | 可变步长 / 归纳变量 | `a[i] += b[LEN_1D-k]`，步长 n3 | 🔒+📐 外层屏蔽；若可见则因步长为运行时变量 n3 |
| **s123** | 条件下归纳变量 | `j++` 在 if 内，`a[j]=...` | 🔒+📝 外层屏蔽；若可见则因 `j` 在 if 内增量，写地址不确定 |
| **s124** | 双分支归纳变量 | `if(b[i]>0){j++;a[j]=...}else{j++;a[j]=...}` | 🔒+📝 外层屏蔽；若可见则因 j 为动态下标写地址，无法向量化 |
| **s125** | 二维归纳变量 | `k++; flat_2d_array[k] = aa[i][j]+...` | 🔒+📝 外层屏蔽；若可见则因 k 为动态写地址（scatter） |
| **s126** | 二维归纳变量 / 递归 | `bb[j][i] = bb[j-1][i] + ...; ++k` | 🔒+🔗+📝 外层屏蔽；若可见则因 `bb[j]←bb[j-1]` RAW 依赖 + k 动态 |
| **s127** | 多增量归纳变量 | `j++; a[j]=...; j++; a[j]=...`，两次 j++ | 🔒+📝 外层屏蔽；若可见则因 j 双增量 scatter |
| **s128** | 耦合归纳变量 | `k=j+1; a[i]=b[k]-d[i]; j=k+1; b[k]=a[i]+c[k]` | 🔒+🔗+📝 外层屏蔽；若可见则因 j/k 耦合 + b[k] RAW 依赖 |
| **s131** | 全局数据流分析 | `a[i] = a[i+m] + b[i]`，m 运行时未知 | 🔒 外层屏蔽；若可见则因 m 未知，a 可能自依赖（工具推断为 RAW） |
| **s132** | 多维歧义下标 | `aa[j][i] = aa[k][i-1] + b[i]*c[1]`，j/k 为外层变量 | 🔒 外层屏蔽；若可见则因 j/k 歧义（不确定 j==k 是否导致依赖） |
| **s141** | 非线性依赖 / 对称压缩数组 | `flat_2d_array[k] += bb[j][i]; k+=j+1` | 🔒+🔣+📐 外层屏蔽；若可见则因 k 非线性 scatter |
| **s151** | 过程间数据流分析 | 循环体含子程序调用 | 🔒+📞 外层屏蔽；若可见则因循环体为函数调用 |
| **s152** | 过程间数据流分析 | `b[i]=d[i]*e[i]; s152s(a,b,c,i)` | 🔒+📞 外层屏蔽；若可见则因含函数调用 `s152s()` |
| **s161** | 控制流 / goto | `if(b[i]<0) goto L20; ... goto L10; L20:...` | 🔒+🔀 外层屏蔽；若可见则因 goto 控制流 |
| **s1161** | 控制流 / goto | `if(c[i]<0) goto L20; ...` | 🔒+🔀 外层屏蔽；若可见则因 goto 控制流 |
| **s162** | 推导断言 | `a[i] = a[i+k] + b[i]*c[i]`，k 运行时 | 🔒 外层屏蔽；若可见则因 k 未知，a 可能自依赖 |
| **s171** | 符号依赖测试 | `a[i*inc] += b[i]`，步长 inc | 🔒+📐 外层屏蔽；若可见则因下标 `i*inc` 非单位步长 |
| **s172** | 符号化 / n3≠0 | `a[i] += b[i]`，步长 n3 | 🔒+📐 外层屏蔽；若可见则因步长为运行时变量 n3 |
| **s173** | 符号化下标 | `a[i+k] = a[i] + b[i]`，k 为偏移量 | 🔒 外层屏蔽；若可见则因 `a[i+k]` 与 `a[i]` 可能别名（k 未知） |
| **s174** | 歧义下标 | `a[i+M] = a[i] + b[i]`，M 为符号量 | 🔒 外层屏蔽；若可见则因 M 未知可能别名 |
| **s175** | 符号依赖测试 | `a[i] = a[i+inc] + b[i]`，步长 inc | 🔒+📐+🔗 外层屏蔽；若可见则因步长 inc + `a[i]←a[i+inc]` 反向依赖 |
| **s176** | 卷积 | `a[i] += b[i+m-j-1] * c[j]`，j 为外层 | 🔒 外层屏蔽；若可见则因 `b[i+m-j-1]` 下标含外层变量 j |
| **s211** | 语句重排 | `a[i]=b[i-1]+c[i]*d[i]; b[i]=b[i+1]-e[i]*d[i]` | 🔒+📝 外层屏蔽；若可见则因两语句均赋值 b，多写依赖 |
| **s212** | 语句重排 / 需临时变量 | `a[i]*=c[i]; b[i]+=a[i+1]*d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `a[i+1]` 在同一迭代被前句修改 |
| **s1213** | 语句重排 / 需临时变量 | `a[i]=b[i-1]+c[i]; b[i]=a[i+1]*d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `a[i+1]` 被后续迭代使用 |
| **s221** | 循环分布 / 部分递归 | `a[i]+=c[i]*d[i]; b[i]=b[i-1]+a[i]+d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `b[i]←b[i-1]` RAW 依赖（第二语句） |
| **s1221** | 运行时符号解析 | `b[i] = b[i-4] + a[i]` | 🔒+🔗 外层屏蔽；若可见则因 `b[i]←b[i-4]` RAW 依赖（距离 4） |
| **s222** | 循环分布 / 中间递归 | `a[i]+=b[i]*c[i]; e[i]=e[i-1]*e[i-1]; a[i]-=...` | 🔒+📝+🔗 外层屏蔽；若可见则因 `e[i]←e[i-1]` RAW + 多语句 |
| **s231** | 循环交换 / 数据依赖 | `aa[j][i] = aa[j-1][i] + bb[j][i]` | 🔒+🔗 外层屏蔽；若可见则因 `aa[j]←aa[j-1]` RAW 依赖 |
| **s232** | 循环交换 / 三角循环 | `aa[j][i] = aa[j][i-1]*aa[j][i-1]+bb[j][i]` | 🔒+🔗 外层屏蔽；若可见则因 `aa[j][i]←aa[j][i-1]` RAW 依赖 |
| **s1232** | 循环交换 / 三角循环 | `aa[i][j] = bb[i][j] + cc[i][j]`（三角范围） | 🔒 外层屏蔽；若可见则因三角循环上界为 i（依赖外层变量） |
| **s233** | 循环交换 / 两内层循环 | `aa[j][i]←aa[j-1][i]`；`bb[j][i]←bb[j][i-1]` | 🔒+🔗 外层屏蔽；若可见则两个内层均有 RAW 依赖 |
| **s2233** | 循环交换 / 两内层循环 | 同 s233 | 🔒+🔗 同 s233 |
| **s235** | 循环交换 / 非完美嵌套 | `aa[j][i]←aa[j-1][i]`（内层有 RAW） | 🔒+🔗 外层屏蔽；若可见则因 `aa[j]←aa[j-1]` RAW 依赖 |
| **s241** | 节点分裂 / 预加载 | `a[i]=b[i]*c[i]*d[i]; b[i]=a[i]*a[i+1]*d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `b[i]` 被修改后读 `a[i+1]` |
| **s242** | 节点分裂 | `a[i] = a[i-1] + s1+s2+b[i]+c[i]+d[i]` | 🔒+🔗 外层屏蔽；若可见则因 `a[i]←a[i-1]` RAW 依赖 |
| **s243** | 节点分裂 / 假依赖环 | `a[i]=b[i]+c[i]*d[i]; b[i]=a[i]+d[i]*e[i]; a[i]=b[i]+a[i+1]*d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因三语句涉及 a/b 交叉 RAW |
| **s244** | 节点分裂 / 假依赖环 | `a[i]=...; b[i]=c[i]+b[i]; a[i+1]=b[i]+a[i+1]*d[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `a[i+1]` 在同迭代被修改 |
| **s1244** | 节点分裂 / 真+反依赖 | `a[i]=...; d[i]=a[i]+a[i+1]` | 🔒+📝 外层屏蔽；若可见则因多语句含 `a[i+1]` 跨迭代读 |
| **s2244** | 节点分裂 / 真+反依赖 | `a[i+1]=b[i]+e[i]; a[i]=b[i]+c[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `a[i+1]` 被前句修改后续迭代读 |
| **s251** | 标量展开 | `s=b[i]+c[i]*d[i]; a[i]=s*s` | 🔒+📝 外层屏蔽；若可见则因 s 为标量临时变量（两语句依赖 s） |
| **s1251** | 标量展开 | `s=b[i]+c[i]; b[i]=a[i]+d[i]; a[i]=s*e[i]` | 🔒+📝 外层屏蔽；若可见则因 s 临时 + 三语句交叉依赖 |
| **s2251** | 标量展开 | `a[i]=s*e[i]; s=b[i]+c[i]; b[i]=a[i]+d[i]` | 🔒+📝 外层屏蔽；若可见则因 s 跨迭代循环携带 |
| **s3251** | 标量展开 | `a[i+1]=b[i]+c[i]; b[i]=c[i]*e[i]; d[i]=a[i]*e[i]` | 🔒+📝+🔗 外层屏蔽；若可见则因 `a[i+1]` 被写后跨迭代读 |
| **s252** | 歧义标量临时变量 | `s=b[i]*c[i]; a[i]=s+t; t=s` | 🔒+📝 外层屏蔽；若可见则因 t 为跨迭代循环携带标量 |
| **s253** | 条件下标量展开 | `if(a[i]>b[i]){s=a[i]-b[i]*d[i]; c[i]+=s; a[i]=s}` | 🔒+📝 外层屏蔽；若可见则因 s 在 if 内定义后多次使用 |
| **s254** | 携带变量 / 1 级 | `a[i]=(b[i]+x)*.5; x=b[i]` | 🔒+📝 外层屏蔽；若可见则因 x 为跨迭代循环携带标量（loop-carried scalar） |
| **s255** | 携带变量 / 2 级 | `a[i]=(b[i]+x+y)*.333; y=x; x=b[i]` | 🔒+📝 外层屏蔽；若可见则因 x、y 双携带标量 |
| **s256** | 数组展开 | `a[j]=1.0-a[j-1]; aa[j][i]=a[j]+bb[j][i]*d[j]` | 🔒+🔗 外层屏蔽；若可见则因 `a[j]←a[j-1]` RAW 依赖 |
| **s257** | 数组展开 | `a[i]=aa[j][i]-a[i-1]; aa[j][i]=a[i]+bb[j][i]` | 🔒+🔗+📝 外层屏蔽；若可见则因 `a[i]←a[i-1]` + 多语句 |
| **s258** | 条件下循环携带标量 | `if(a[i]>0){s=d[i]*d[i]}; b[i]=s*c[i]+d[i]` | 🔒+📝 外层屏蔽；若可见则因 s 跨迭代循环携带（无 else 时值延续） |
| **s261** | 标量展开 / 循环携带 | `t=a[i]+b[i]; a[i]=t+c[i-1]; t=c[i]*d[i]; c[i]=t` | 🔒+📝+🔗 外层屏蔽；若可见则因 t 双重使用 + `c[i]←c[i-1]` |
| **s271** | 奇点处理 / if | `if(b[i]>0) a[i]+=b[i]*c[i]` | 🔒 外层屏蔽；若可见则**可向量化**（CONDITIONAL 模式，单 if-then） |
| **s272** | 独立条件 if | `if(e[i]>=t){a[i]+=c[i]*d[i]; b[i]+=c[i]*c[i]}` | 🔒+📝 外层屏蔽；若可见则因 if 内含两条赋值（多写） |
| **s273** | 依赖条件 if | `a[i]+=d[i]*e[i]; if(a[i]<0) b[i]+=...; c[i]+=a[i]*d[i]` | 🔒+📝 外层屏蔽；若可见则因三语句含依赖 a[i] 的条件 |
| **s274** | 复杂依赖 if | `a[i]=c[i]+e[i]*d[i]; if(a[i]>0){b[i]=a[i]+b[i]}else{a[i]=...}` | 🔒+📝 外层屏蔽；若可见则因 if-else 含多写 + 依赖 a[i] |
| **s275** | if 包裹内层循环 | `if(aa[0][i]>0){for j: aa[j][i]=aa[j-1][i]+bb[j][i]*cc[j][i]}` | 🔒+🔗 外层屏蔽；若可见则因内层 `aa[j]←aa[j-1]` RAW 依赖 |
| **s2275** | 需分布才能交换 | `for j: aa[j][i]+=...; a[i]=b[i]+c[i]*d[i]` | 🔒+📝 外层屏蔽；若可见则因 for-j 后还有 a[i] 赋值（混合体） |
| **s276** | 使用循环下标的 if | `if(i+1<mid){a[i]+=b[i]*c[i]}else{a[i]+=b[i]*d[i]}` | 🔒 外层屏蔽；若可见则因 if 条件含循环变量 i（工具不支持此类谓词） |
| **s277** | 守卫变量 / goto | `if(a[i]>=0) goto L20; if(b[i]>=0) goto L30; ...` | 🔒+🔀 外层屏蔽；若可见则因多 goto 控制流 |
| **s278** | if/goto 模拟 if-then-else | `if(a[i]>0) goto L20; b[i]=...; goto L30; L20: c[i]=...` | 🔒+🔀 外层屏蔽；若可见则因 goto 控制流 |
| **s279** | 向量 if/goto | `if(a[i]>0) goto L20; ... if(b[i]<=a[i]) goto L30; ...` | 🔒+🔀 外层屏蔽；若可见则因多分支 goto |
| **s1279** | 向量 if/goto | `if(a[i]<0){if(b[i]>a[i]){c[i]+=d[i]*e[i]}}` | 🔒 外层屏蔽；若可见则因嵌套 if（工具仅支持单层 if） |
| **s2710** | 标量和向量 if | `if(a[i]>b[i]){a[i]+=b[i]*d[i]; if(LEN>10){...}else{...}}` | 🔒+📝 外层屏蔽；若可见则因嵌套 if + 多写 |
| **s2711** | 语义 if 消除 | `if(b[i]!=0.0) a[i]+=b[i]*c[i]` | 🔒 外层屏蔽；若可见则**可向量化**（等价 CONDITIONAL，`!=` 运算符） |
| **s2712** | if 转 elemental min | `if(a[i]>b[i]) a[i]+=b[i]*c[i]` | 🔒 外层屏蔽；若可见则**可向量化**（CONDITIONAL 模式） |
| **s281** | 越阈值 / 逆序访问 | `x=a[LEN-i-1]+b[i]*c[i]; a[i]=x-1.0; b[i]=x` | 🔒+📝 外层屏蔽；若可见则因 x 临时标量 + 两写 |
| **s1281** | 越阈值 / 逆序访问 | `x=b[i]*c[i]+a[i]*d[i]+e[i]; a[i]=x-1.0; b[i]=x` | 🔒+📝 外层屏蔽；若可见则因 x 临时标量 + 两写 |
| **s291** | 循环剥离 / 1 级携带 | `a[i]=(b[i]+b[im1])*.5; im1=i` | 🔒+📝 外层屏蔽；若可见则因 im1 跨迭代循环携带标量 |
| **s292** | 循环剥离 / 2 级携带 | `a[i]=(b[i]+b[im1]+b[im2])*.333; im2=im1; im1=i` | 🔒+📝 外层屏蔽；若可见则因 im1/im2 双层循环携带标量 |
| **s293** | `a(i)=a(0)` 有依赖但可向量化 | `a[i] = a[0]` | 🔒 外层屏蔽；若可见则因 `a[0]` 写别名（实际可向量化，工具无法判断） |
| **s2101** | 对角线 / 跳跃访问 | `aa[i][i] += bb[i][i]*cc[i][i]` | 🔒+📐 外层屏蔽；若可见则因对角线下标 `[i][i]` 非行连续步长 |
| **s2102** | 单位矩阵 / 两循环 | `for j: aa[j][i]=0.; aa[i][i]=1.` | 🔒+📝 外层屏蔽；若可见则因内层后还有 `aa[i][i]=1.` 赋值（混合） |
| **s2111** | 波前计算 | `aa[j][i] = (aa[j][i-1]+aa[j-1][i])/1.9` | 🔒+🔗 外层屏蔽；若可见则因 `aa[j][i]←aa[j][i-1]` 和 `←aa[j-1][i]` 双 RAW 依赖 |
| **s311** | 求和归约 | `sum += a[i]` | 🔒 外层屏蔽；若可见则**可向量化**（REDUCTION ADD 模式）；注意 sum 函数内另有内部小 REDUCTION 成功 |
| **s31111** | 求和归约 | （无独立 i 循环体可提取） | 🔒 外层屏蔽（且提取不到内层循环体） |
| **s312** | 积归约 | `prod *= a[i]` | 🔒 外层屏蔽；若可见则**可向量化**（REDUCTION MUL 模式） |
| **s313** | 点积 | `dot += a[i]*b[i]` | 🔒 外层屏蔽；若可见则**可向量化**（REDUCTION ADD 模式） |
| **s314** | if 转 max 归约 | `if(a[i]>x) x=a[i]` | 🔒 外层屏蔽；若可见则因 max 归约工具不支持（仅支持 ADD/MUL） |
| **s315** | 带索引的 max 归约 | `if(a[i]>x){x=a[i]; index=i}` | 🔒+📝 外层屏蔽；若可见则因含 index 更新（多写）+ max 归约不支持 |
| **s316** | if 转 min 归约 | `if(a[i]<x) x=a[i]` | 🔒 外层屏蔽；若可见则因 min 归约工具不支持 |
| **s317** | 积归约 / 标量展开 | `q *= 0.99` | 🔒 外层屏蔽；若可见则**可向量化**（REDUCTION MUL 常量） |
| **s318** | isamax / 非单位步长 | `if(ABS(a[k])<=max) goto L5; ...; k+=inc` | 🔒+🔀+📐 外层屏蔽；若可见则因 goto + 步长 inc |
| **s319** | 耦合归约 | `a[i]=c[i]+d[i]; sum+=a[i]; b[i]=c[i]+e[i]; sum+=b[i]` | 🔒+📝 外层屏蔽；若可见则因多语句 + sum 两次更新 |
| **s3110** | 二维 max 归约 | `if(aa[i][j]>max){max=aa[i][j]; xindex=i; yindex=j}` | 🔒+📝 外层屏蔽；若可见则因 max 归约不支持 + 多写 |
| **s13110** | 二维 max 归约 | 同 s3110 | 🔒+📝 同 s3110 |
| **s3111** | 条件求和归约 | `if(a[i]>0) sum+=a[i]` | 🔒 外层屏蔽；若可见则因条件归约工具不支持（目前 REDUCTION 不含 if） |
| **s3112** | 保存滚动和 | `sum+=a[i]; b[i]=sum` | 🔒+📝 外层屏蔽；若可见则因 sum 既是归约又被写入 b[i]（前缀和，有依赖） |
| **s3113** | 绝对值 max 归约 | `if(ABS(a[i])>max) max=ABS(a[i])` | 🔒+📞 外层屏蔽；若可见则因 ABS 函数调用 + max 归约不支持 |
| **s321** | 一阶线性递归 | `a[i] += a[i-1]*b[i]` | 🔒+🔗 外层屏蔽；若可见则因 `a[i]←a[i-1]` RAW 依赖 |
| **s322** | 二阶线性递归 | `a[i]=a[i]+a[i-1]*b[i]+a[i-2]*c[i]` | 🔒+🔗 外层屏蔽；若可见则因 `a[i]←a[i-1]`、`←a[i-2]` 双 RAW 依赖 |
| **s323** | 耦合递归 | `a[i]=b[i-1]+c[i]*d[i]; b[i]=a[i]+c[i]*e[i]` | 🔒+🔗+📝 外层屏蔽；若可见则因 `b[i]←b[i-1]` RAW + 两语句 |
| **s331** | 搜索循环 / last-1 | `if(a[i]<0) j=i` | 🔒+📝 外层屏蔽；若可见则因 j 为条件赋值标量（搜索循环，无法向量化） |
| **s332** | 搜索首个超阈值 | `if(a[i]>t){index=i; value=a[i]; goto L20}` | 🔒+🔀 外层屏蔽；若可见则因 goto 提前退出 |
| **s341** | 压缩 / 条件 scatter | `if(b[i]>0){j++;a[j]=b[i]}` | 🔒+🔣+📝 外层屏蔽；若可见则因 j 动态下标 scatter |
| **s342** | 解压 / 条件 gather | `if(a[i]>0){j++;a[i]=b[j]}` | 🔒+🔣+📝 外层屏蔽；若可见则因 j 动态下标 gather |
| **s343** | 二维压缩 | `if(bb[j][i]>0){k++;flat[k]=aa[j][i]}` | 🔒+🔣+📝 外层屏蔽；若可见则因 k 动态 scatter |
| **s351** | 循环重卷 / 展开 saxpy | `a[i]+=alpha*b[i]; a[i+1]+=...; ...`，步长 5 | 🔒+📐 外层屏蔽；若可见则因步长 5 + 多语句 |
| **s1351** | 归纳指针识别 | `*A=*B+*C; A++; B++; C++` | 🔒+📝 外层屏蔽；若可见则因指针算术（工具不解析指针递增） |
| **s352** | 循环重卷 / 展开点积 | `dot+=a[i]*b[i]+a[i+1]*b[i+1]+...`，步长 5 | 🔒+📐+📝 外层屏蔽；若可见则因步长 5 + 多项求和 |
| **s353** | 循环重卷 / 稀疏 saxpy | `a[i]+=alpha*b[ip[i]]`，步长 5 | 🔒+🔣+📐 外层屏蔽；若可见则因 gather `b[ip[i]]` + 步长 5 |
| **s421** | 等价 / 无重叠 | `xx[i] = yy[i+1] + a[i]` | 🔒 外层屏蔽；若可见则**可向量化**（ELEMENTWISE，xx/yy 无别名） |
| **s1421** | 等价 / 无重叠 | `b[i] = xx[i] + a[i]` | 🔒 外层屏蔽；若可见则**可向量化**（ELEMENTWISE） |
| **s422** | 公共区 / 反依赖 | `xx[i] = flat_2d_array[i+8] + a[i]` | 🔒 外层屏蔽；若可见则**可向量化**（偏移 8，无真实依赖） |
| **s423** | 公共区 / 反依赖 | `flat_2d_array[i+1] = xx[i] + a[i]` | 🔒 外层屏蔽；若可见则因 `flat[i+1]←xx[i]` 可能别名（工具保守拒绝） |
| **s424** | 公共区 / 重叠 | `xx[i+1] = flat_2d_array[i] + a[i]` | 🔒+🔗 外层屏蔽；若可见则因 `xx[i+1]` 与 `flat[i]` 可能重叠 RAW |
| **s431** | 参数 / 符号偏移 | `a[i] = a[i+k] + b[i]`，k 运行时 | 🔒 外层屏蔽；若可见则因 k 未知，`a[i]` 与 `a[i+k]` 可能别名 |
| **s441** | 算术 if（三分支） | `if(d[i]<0){a[i]+=b[i]*c[i]}else if(d[i]==0){a[i]+=...}else{...}` | 🔒+📝 外层屏蔽；若可见则因 else-if 链（工具仅支持单 if-then） |
| **s442** | 计算 goto / switch | `switch(indx[i]){case 1: goto...; case 2: goto...}` | 🔒+🔀 外层屏蔽；若可见则因 switch/goto 控制流 |
| **s443** | 算术 if / goto | `if(d[i]<=0) goto L20; else goto L30; L20:... L30:...` | 🔒+🔀 外层屏蔽；若可见则因 goto 控制流 |
| **s451** | 内建函数 / 三角函数 | `a[i] = sinf(b[i]) + cosf(c[i])` | 🔒+📞 外层屏蔽；若可见则因 `sinf/cosf` 函数调用（工具不展开数学函数） |
| **s452** | seq 函数 / 循环下标 | `a[i] = b[i] + c[i]*(real_t)(i+1)` | 🔒 外层屏蔽；若可见则因下标含循环变量 i 的整数表达式（工具不支持 seq 向量） |
| **s453** | 归纳变量识别 | `s += 2.0; a[i] = s*b[i]` | 🔒+📝 外层屏蔽；若可见则因 s 为循环携带累积标量 |
| **s471** | 函数调用 | `x[i]=b[i]+d[i]*d[i]; s471s(); b[i]=c[i]+d[i]*e[i]` | 🔒+📞+📝 外层屏蔽；若可见则因 `s471s()` 函数调用（可能有副作用） |
| **s481** | 非局部 goto / exit | `if(d[i]<0) exit(0); a[i]+=b[i]*c[i]` | 🔒+🔀 外层屏蔽；若可见则因 `exit()` 导致控制流中断 |
| **s482** | 非局部 goto / break | `a[i]+=b[i]*c[i]; if(c[i]>b[i]) break` | 🔒+🔀 外层屏蔽；若可见则因 `break` 提前退出（工具不识别 break） |
| **s491** | 间接寻址 / LHS scatter | `a[ip[i]] = b[i] + c[i]*d[i]` | 🔒+🔣 外层屏蔽；若可见则因 `a[ip[i]]` 为 scatter 写（工具不支持） |
| **s4112** | 间接寻址 / 稀疏 saxpy | `a[i] += b[ip[i]] * s` | 🔒+🔣 外层屏蔽；若可见则因 `b[ip[i]]` 为 gather 读（工具不支持） |
| **s4113** | 间接寻址 / 双侧 | `a[ip[i]] = b[ip[i]] + c[i]` | 🔒+🔣 外层屏蔽；若可见则因 gather+scatter 双侧间接寻址 |
| **s4114** | 间接寻址 / 可变边界 | `k=ip[i]; a[i]=b[i]+c[LEN-k+1-2]*d[i]; k+=5` | 🔒+🔣 外层屏蔽；若可见则因 `c[LEN-k+1-2]` 间接下标 |
| **s4115** | 间接寻址 / 稀疏点积 | `sum += a[i]*b[ip[i]]` | 🔒+🔣 外层屏蔽；若可见则因 `b[ip[i]]` gather |
| **s4116** | 更复杂稀疏点积 | `sum += a[off]*aa[j-1][ip[i]]`，off=inc+i | 🔒+🔣 外层屏蔽；若可见则因 `aa[j-1][ip[i]]` 二维 gather |
| **s4117** | 间接寻址 / seq | `a[i] = b[i] + c[i/2]*d[i]` | 🔒+📐 外层屏蔽；若可见则因 `c[i/2]` 非单位步长（步长 0.5） |
| **s4121** | 语句函数 / 多类型测试 | 包含 scatter/gather/conditional/reduction 等多种模式 | 🔒+🔣 外层屏蔽；函数内含多类子函数，各自为 ELEMENTWISE、gather、scatter 等混合 |
