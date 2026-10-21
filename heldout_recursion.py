# Factorial
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)


# Naive Fibonacci
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


# Tower of Hanoi (count moves)
def hanoi(n):
    if n == 0:
        return 0
    return hanoi(n - 1) + 1 + hanoi(n - 1)


# Merge helper for merge sort
def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result


# Merge sort
def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)


# Wrapper: merge sort on a reversed list of size n
def run_merge_sort(n):
    return merge_sort(list(range(n, 0, -1)))


# Quicksort
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[0]
    less = [x for x in arr[1:] if x < pivot]
    greater = [x for x in arr[1:] if x >= pivot]
    return quicksort(less) + [pivot] + quicksort(greater)


# Wrapper: quicksort on a deterministic shuffled list of size n
def run_quicksort(n):
    return quicksort([(i * 37) % 101 for i in range(n)])


# Recursive binary search
def binary_search(arr, target, lo, hi):
    if lo > hi:
        return -1
    mid = (lo + hi) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search(arr, target, mid + 1, hi)
    else:
        return binary_search(arr, target, lo, mid - 1)


# Wrapper: binary search for the last element of a sorted list of size n
def run_binary_search(n):
    arr = list(range(n))
    return binary_search(arr, n - 1, 0, len(arr) - 1)


# Recursive sum of a list
def list_sum(arr):
    if not arr:
        return 0
    return arr[0] + list_sum(arr[1:])


# Wrapper: sum of the list [1, 2, ..., n]
def run_list_sum(n):
    return list_sum(list(range(1, n + 1)))


# Generate all subsets (power set)
def subsets(nums):
    if not nums:
        return [[]]
    rest = subsets(nums[1:])
    return rest + [[nums[0]] + s for s in rest]


# Wrapper: power set of [1, 2, ..., n]
def run_subsets(n):
    return subsets(list(range(1, n + 1)))
