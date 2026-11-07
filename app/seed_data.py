"""内置参考数据：知识点树 + 起始题库。

题库只含元信息与外链，不含任何第三方题面/样例/题解原文（NFR-05）。
题目均为公开题库中真实存在的经典题，链接可直接跳转体验（FR-01 / TC-05）。
"""
from __future__ import annotations

# (name, category, children)
KNOWLEDGE_TREE = [
    ("基础与数学", "knowledge", [
        ("基础语法", []), ("模拟", []), ("枚举", []), ("排序", []),
        ("数学", [("数论", []), ("组合数学", []), ("概率与期望", []), ("计算几何", []), ("矩阵运算", [])]),
    ]),
    ("数据结构", "data_structure", [
        ("线性结构", [("数组", []), ("链表", []), ("栈", []), ("队列", []), ("单调栈", []), ("单调队列", [])]),
        ("树形结构", [("二叉树", []), ("并查集", []), ("线段树", []), ("树状数组", []), ("字典树", []), ("堆", [])]),
        ("哈希与字符串", [("哈希表", []), ("字符串匹配", []), ("KMP", []), ("字符串哈希", [])]),
    ]),
    ("算法", "algorithm", [
        ("基础算法", [("递归", []), ("分治", []), ("二分", []), ("前缀和/差分", []), ("贪心", []),
                  ("双指针", []), ("滑动窗口", []), ("构造", [])]),
        ("搜索", [("DFS", []), ("BFS", []), ("回溯", []), ("剪枝", []), ("记忆化搜索", [])]),
        ("动态规划", [("线性DP", []), ("背包DP", []), ("区间DP", []), ("树形DP", []), ("状压DP", []), ("数位DP", [])]),
        ("图论", [("最短路", []), ("最小生成树", []), ("拓扑排序", []), ("连通性", []),
                 ("二分图", []), ("网络流", []), ("博弈论", [])]),
    ]),
]


def _p(source, key, title, url, raw, level, tags, nodes, summary=""):
    return {
        "source": source, "source_key": key, "title": title, "url": url,
        "difficulty_raw": raw, "difficulty_level": level, "external_tags": tags,
        "nodes": nodes, "summary": summary,
    }


_LUOGU = "https://www.luogu.com.cn/problem/"
_LC = "https://leetcode.cn/problems/"
_CF = "https://codeforces.com/problemset/problem/"
_AT = "https://atcoder.jp/contests/"
_LQ = "https://www.lanqiao.cn/problems/"

STARTER_PROBLEMS = [
    # ---- 洛谷：基础 → 进阶 ----
    _p("luogu", "P1001", "A+B Problem", _LUOGU + "P1001", "入门", 1, ["模拟", "数学"], ["基础语法", "模拟"], "基本输入输出与加法运算"),
    _p("luogu", "P1003", "铺地毯", _LUOGU + "P1003", "入门", 1, ["模拟", "枚举"], ["模拟", "枚举"], "逆序枚举矩形覆盖判定"),
    _p("luogu", "P1008", "三连击", _LUOGU + "P1008", "入门", 1, ["枚举", "模拟"], ["枚举", "模拟"], "全排列枚举与数位拆分"),
    _p("luogu", "P1421", "小玉买文具", _LUOGU + "P1421", "入门", 1, ["数学"], ["基础语法", "数学"], "浮点与整除基础"),
    _p("luogu", "P5717", "三角形分类", _LUOGU + "P5717", "入门", 1, ["模拟", "数学"], ["模拟", "数学"], "排序后勾股定理判定"),
    _p("luogu", "P1048", "采药", _LUOGU + "P1048", "普及-", 2, ["动态规划", "背包"], ["背包DP", "动态规划"], "01 背包模板题"),
    _p("luogu", "P1049", "装箱问题", _LUOGU + "P1049", "普及-", 2, ["动态规划", "背包"], ["背包DP", "动态规划"], "01 背包最小剩余体积"),
    _p("luogu", "P1616", "完全背包", _LUOGU + "P1616", "普及-", 2, ["动态规划", "背包"], ["背包DP", "动态规划"], "完全背包模板"),
    _p("luogu", "P1216", "数字三角形", _LUOGU + "P1216", "普及-", 2, ["动态规划"], ["线性DP", "动态规划"], "自底向上线性 DP"),
    _p("luogu", "P1002", "过河卒", _LUOGU + "P1002", "普及-", 2, ["动态规划", "搜索"], ["线性DP", "动态规划"], "带障碍的路径计数 DP"),
    _p("luogu", "P1020", "导弹拦截", _LUOGU + "P1020", "普及/提高-", 3, ["动态规划", "二分"], ["线性DP", "二分"], "最长不升子序列与贪心覆盖"),
    _p("luogu", "P1090", "合并果子", _LUOGU + "P1090", "普及-", 2, ["贪心", "堆"], ["堆", "贪心"], "优先队列哈夫曼合并"),
    _p("luogu", "P2249", "查找", _LUOGU + "P2249", "普及-", 2, ["二分"], ["二分"], "有序数组二分查找"),
    _p("luogu", "P1102", "A-B 数对", _LUOGU + "P1102", "普及-", 2, ["二分", "哈希表"], ["哈希表", "二分"], "哈希计数或双指针统计"),
    _p("luogu", "P1443", "马的遍历", _LUOGU + "P1443", "普及-", 2, ["搜索", "BFS"], ["BFS"], "BFS 最短步数"),
    _p("luogu", "P1036", "选数", _LUOGU + "P1036", "普及-", 2, ["搜索", "数论"], ["DFS", "数论"], "组合枚举 + 素数判定"),
    _p("luogu", "P1739", "表达式括号匹配", _LUOGU + "P1739", "入门", 1, ["栈", "模拟"], ["栈", "模拟"], "栈计数括号合法"),
    _p("luogu", "P1990", "覆盖墙壁", _LUOGU + "P1990", "普及-", 2, ["动态规划"], ["线性DP", "动态规划"], "状态机递推"),
    _p("luogu", "P3371", "单源最短路径（弱化版）", _LUOGU + "P3371", "普及/提高-", 3, ["图论", "最短路"], ["最短路", "图论"], "Dijkstra 模板"),
    _p("luogu", "P3366", "最小生成树", _LUOGU + "P3366", "普及/提高-", 3, ["图论", "最小生成树"], ["最小生成树", "图论"], "Prim / Kruskal"),
    _p("luogu", "P3372", "线段树 1", _LUOGU + "P3372", "普及+/提高", 3, ["线段树", "数据结构"], ["线段树"], "区间加与区间求和"),
    _p("luogu", "P3370", "字符串哈希", _LUOGU + "P3370", "普及-", 2, ["字符串", "哈希"], ["字符串哈希", "哈希表"], "哈希去重"),
    _p("luogu", "P3383", "线性筛素数", _LUOGU + "P3383", "普及-", 2, ["数论", "数学"], ["数论", "数学"], "欧拉筛"),
    _p("luogu", "P3367", "并查集", _LUOGU + "P3367", "普及-", 2, ["并查集", "数据结构"], ["并查集"], "并查集模板"),
    _p("luogu", "P2670", "扫雷游戏", _LUOGU + "P2670", "入门", 1, ["模拟"], ["模拟", "数组"], "二维网格八邻域统计"),
    _p("luogu", "P1601", "A+B Problem（高精）", _LUOGU + "P1601", "普及-", 2, ["高精度", "模拟"], ["模拟", "数组"], "竖式加法高精度"),
    _p("luogu", "P1303", "A*B Problem", _LUOGU + "P1303", "普及-", 2, ["高精度", "模拟"], ["模拟", "数组"], "高精度乘法"),
    _p("luogu", "P1057", "传球游戏", _LUOGU + "P1057", "普及-", 2, ["动态规划"], ["线性DP", "动态规划"], "环形递推 DP"),
    _p("luogu", "P1017", "进制转换", _LUOGU + "P1017", "普及-", 2, ["数学", "模拟"], ["数学", "模拟"], "负进制取余修正"),
    _p("luogu", "P1060", "开心的金明", _LUOGU + "P1060", "普及-", 2, ["动态规划", "背包"], ["背包DP", "动态规划"], "01 背包价值最大化"),
    _p("luogu", "P1024", "一元三次方程求解", _LUOGU + "P1024", "普及-", 2, ["二分", "数学"], ["二分", "数学"], "浮点二分求根"),
    _p("luogu", "P1116", "车厢重组", _LUOGU + "P1116", "入门", 1, ["排序", "模拟"], ["排序", "模拟"], "逆序对计数"),

    # ---- Codeforces：官方公开 API 元信息对应的题 ----
    _p("codeforces", "4A", "Watermelon", _CF + "4/A", "800", 1, ["brute force", "math"], ["枚举", "数学"], "偶数可分成两个正偶数"),
    _p("codeforces", "1A", "Theatre Square", _CF + "1/A", "1000", 2, ["math"], ["数学"], "向上取整的网格铺砖"),
    _p("codeforces", "71A", "Way Too Long Words", _CF + "71/A", "800", 1, ["strings", "implementation"], ["字符串匹配", "模拟"], "长单词缩写"),
    _p("codeforces", "231A", "Team", _CF + "231/A", "800", 1, ["implementation"], ["模拟"], "统计至少有两人有把握的题"),
    _p("codeforces", "263A", "Beautiful Matrix", _CF + "263/A", "800", 1, ["implementation"], ["模拟", "数组"], "曼哈顿距离移动 1"),
    _p("codeforces", "282A", "Bit++", _CF + "282/A", "800", 1, ["implementation"], ["模拟", "基础语法"], "简单变量自增"),
    _p("codeforces", "158A", "Next Round", _CF + "158/A", "800", 1, ["implementation"], ["模拟", "排序"], "计分晋级人数"),
    _p("codeforces", "617A", "Elephant", _CF + "617/A", "800", 1, ["greedy", "math"], ["贪心", "枚举"], "最少步数贪心"),
    _p("codeforces", "705A", "Hulk", _CF + "705/A", "800", 1, ["implementation"], ["模拟", "构造"], "交替字符串构造"),
    _p("codeforces", "791A", "Bear and Big Brother", _CF + "791/A", "800", 1, ["implementation", "math"], ["模拟", "数学"], "倍数增长模拟"),
    _p("codeforces", "977A", "Wrong Subtraction", _CF + "977/A", "800", 1, ["implementation"], ["模拟"], "按规则迭代"),
    _p("codeforces", "734A", "Anton and Danik", _CF + "734/A", "800", 1, ["strings", "implementation"], ["模拟", "字符串匹配"], "字符计数比较"),
    _p("codeforces", "467A", "George and Accommodation", _CF + "467/A", "800", 1, ["implementation"], ["模拟", "枚举"], "双人间空位统计"),
    _p("codeforces", "200B", "Drinks", _CF + "200/B", "800", 1, ["math", "implementation"], ["数学", "模拟"], "浓度加权平均"),
    _p("codeforces", "510A", "Fox And Snake", _CF + "510/A", "800", 1, ["implementation"], ["模拟", "数组"], "蛇形图案输出"),

    # ---- LeetCode（中国站）----
    _p("leetcode", "two-sum", "两数之和", _LC + "two-sum/", "简单", 1, ["array", "hash-table"], ["哈希表", "数组"], "哈希表单遍查找补数"),
    _p("leetcode", "valid-parentheses", "有效的括号", _LC + "valid-parentheses/", "简单", 1, ["stack", "string"], ["栈", "字符串匹配"], "栈匹配括号"),
    _p("leetcode", "climbing-stairs", "爬楼梯", _LC + "climbing-stairs/", "简单", 1, ["dynamic-programming"], ["线性DP", "动态规划"], "斐波那契递推"),
    _p("leetcode", "maximum-subarray", "最大子数组和", _LC + "maximum-subarray/", "简单", 2, ["array", "dynamic-programming"], ["线性DP", "数组"], "Kadane 算法"),
    _p("leetcode", "house-robber", "打家劫舍", _LC + "house-robber/", "中等", 2, ["dynamic-programming"], ["线性DP", "动态规划"], "状态机 DP"),
    _p("leetcode", "coin-change", "零钱兑换", _LC + "coin-change/", "中等", 3, ["dynamic-programming"], ["背包DP", "动态规划"], "完全背包最小数"),
    _p("leetcode", "longest-common-subsequence", "最长公共子序列", _LC + "longest-common-subsequence/", "中等", 3, ["dynamic-programming"], ["线性DP", "动态规划"], "二维 LCS DP"),
    _p("leetcode", "number-of-islands", "岛屿数量", _LC + "number-of-islands/", "中等", 3, ["graph", "dfs"], ["DFS", "图论"], "网格 DFS/BFS 连通块"),
    _p("leetcode", "course-schedule", "课程表", _LC + "course-schedule/", "中等", 3, ["graph", "topological-sort"], ["拓扑排序", "图论"], "拓扑排序判环"),
    _p("leetcode", "reverse-linked-list", "反转链表", _LC + "reverse-linked-list/", "简单", 1, ["linked-list"], ["链表"], "指针原地反转"),
    _p("leetcode", "binary-tree-inorder-traversal", "二叉树的中序遍历", _LC + "binary-tree-inorder-traversal/", "简单", 1, ["tree", "stack"], ["二叉树", "栈"], "递归/迭代中序"),
    _p("leetcode", "merge-intervals", "合并区间", _LC + "merge-intervals/", "中等", 2, ["sorting", "array"], ["排序", "数组"], "排序后线性合并"),
    _p("leetcode", "container-with-most-water", "盛最多水的容器", _LC + "container-with-most-water/", "中等", 2, ["two-pointers", "greedy"], ["双指针", "贪心"], "双指针收缩"),
    _p("leetcode", "3sum", "三数之和", _LC + "3sum/", "中等", 3, ["two-pointers", "sorting"], ["双指针", "排序"], "排序 + 去重双指针"),

    # ---- AtCoder ----
    _p("atcoder", "abc001_1", "積雪深差", _AT + "abc001/tasks/abc001_1", "10", 1, ["math"], ["数学", "基础语法"], "两数相减"),
    _p("atcoder", "abc010_1", "足し算", _AT + "abc010/tasks/abc010_1", "10", 1, ["math"], ["基础语法", "模拟"], "加法与字符串拼接"),
    _p("atcoder", "abc086_a", "Product", _AT + "abc086/tasks/abc086_a", "10", 1, ["math"], ["数学", "枚举"], "奇偶性判定"),
    _p("atcoder", "abc104_a", "Rated for Me", _AT + "abc104/tasks/abc104_a", "10", 1, ["implementation"], ["模拟", "枚举"], "分段评级判断"),
    _p("atcoder", "abc113_a", "Discount Fare", _AT + "abc113/tasks/abc113_a", "10", 1, ["math"], ["模拟", "数学"], "两段票价求和"),
    _p("atcoder", "abc169_a", "Multiplication 1", _AT + "abc169/tasks/abc169_a", "10", 1, ["math"], ["数学", "基础语法"], "简单乘法"),

    # ---- 蓝桥杯官网题库 ----
    _p("lanqiao", "1", "A+B 问题（入门）", _LQ + "1/learning/", "入门", 1, ["模拟"], ["基础语法", "模拟"], "基础输入输出"),
    _p("lanqiao", "2", "阶乘计算", _LQ + "2/learning/", "基础", 2, ["数学", "高精度"], ["数学", "数组"], "大数乘法模拟"),
    _p("lanqiao", "3", "Fibonacci 数列", _LQ + "3/learning/", "基础", 2, ["递推", "数学"], ["线性DP", "递归"], "递推与取模"),
    _p("lanqiao", "4", "冒泡排序", _LQ + "4/learning/", "基础", 2, ["排序"], ["排序", "数组"], "基础排序算法"),
    _p("lanqiao", "5", "二分查找", _LQ + "5/learning/", "基础", 2, ["二分"], ["二分", "数组"], "有序序列查找"),
    _p("lanqiao", "6", "数字三角形", _LQ + "6/learning/", "进阶", 3, ["动态规划"], ["线性DP", "动态规划"], "路径最大和 DP"),
]
