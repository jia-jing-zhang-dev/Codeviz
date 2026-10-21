# Bubble sort (counts swaps)
def bubble_sort(n):
    arr = list(range(n, 0, -1))
    swaps = 0
    for i in range(n - 1):
        for j in range(n - 1 - i):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swaps += 1
    return swaps


# Insertion sort
def insertion_sort(n):
    arr = list(range(n, 0, -1))
    for i in range(1, n):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr


# Selection sort
def selection_sort(n):
    arr = list(range(n, 0, -1))
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
    return arr


# Naive matrix multiplication (triple loop)
def matrix_multiply(n):
    A = [[i + j for j in range(n)] for i in range(n)]
    B = [[i * j for j in range(n)] for i in range(n)]
    C = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            for k in range(n):
                C[i][j] += A[i][k] * B[k][j]
    return C


# Pascal's triangle rows
def pascals_triangle(n):
    triangle = []
    for i in range(n):
        row = [1] * (i + 1)
        for j in range(1, i):
            row[j] = triangle[i - 1][j - 1] + triangle[i - 1][j]
        triangle.append(row)
    return triangle


# Count all pairs i < j
def count_pairs(n):
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            count += 1
    return count


# Sum of squares 1..n
def sum_of_squares(n):
    total = 0
    for i in range(1, n + 1):
        total += i * i
    return total


# Multiplication table
def multiplication_table(n, m):
    table = []
    for i in range(1, n + 1):
        row = []
        for j in range(1, m + 1):
            row.append(i * j)
        table.append(row)
    return table


# Prefix sums
def prefix_sums(n):
    arr = [i * 2 + 1 for i in range(n)]
    prefix = [0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + arr[i]
    return prefix


# Binary search (counts steps)
def binary_search(n, target):
    arr = list(range(n))
    low = 0
    high = n - 1
    steps = 0
    while low <= high:
        steps += 1
        mid = (low + high) // 2
        if arr[mid] == target:
            return steps
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return steps


# Sieve of Eratosthenes
def sieve_of_eratosthenes(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = False
    if n >= 1:
        is_prime[1] = False
    p = 2
    while p * p <= n:
        if is_prime[p]:
            for multiple in range(p * p, n + 1, p):
                is_prime[multiple] = False
        p += 1
    primes = []
    for i in range(n + 1):
        if is_prime[i]:
            primes.append(i)
    return primes


# Count all subarrays (i <= j)
def count_subarrays(n):
    arr = list(range(1, n + 1))
    count = 0
    for i in range(len(arr)):
        for j in range(i, len(arr)):
            count += 1
    return count
