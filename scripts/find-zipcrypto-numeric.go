// Command find-zipcrypto-numeric quickly filters numeric ZipCrypto passwords.
// Every reported candidate passes the ZIP password byte and a 4 KiB DEFLATE
// probe; callers must still perform a complete CRC verification.
package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"compress/flate"
	"flag"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
)

const (
	encryptionHeaderSize = 12
	probeEncryptedBytes  = 64 * 1024
	probePlainBytes      = 4 * 1024
	quickEncryptedBytes  = 512
	quickPlainBytes      = 128
	workChunk            = 2048
)

var crcTable = crc32.MakeTable(crc32.IEEE)

type cryptoState struct {
	key0 uint32
	key1 uint32
	key2 uint32
}

type encryptedTarget struct {
	encrypted        []byte
	checkByte        byte
	crc              uint32
	uncompressedSize uint64
}

func crcByte(crc uint32, value byte) uint32 {
	return (crc >> 8) ^ crcTable[byte(crc)^value]
}

func newCryptoState(password []byte) cryptoState {
	state := cryptoState{key0: 0x12345678, key1: 0x23456789, key2: 0x34567890}
	for _, value := range password {
		state.update(value)
	}
	return state
}

func (state *cryptoState) update(plain byte) {
	state.key0 = crcByte(state.key0, plain)
	state.key1 = (state.key1+(state.key0&0xff))*134775813 + 1
	state.key2 = crcByte(state.key2, byte(state.key1>>24))
}

func (state *cryptoState) decrypt(cipher byte) byte {
	temporary := state.key2 | 2
	plain := cipher ^ byte((temporary*(temporary^1))>>8)
	state.update(plain)
	return plain
}

func decimalPassword(number uint64, width int, buffer []byte) []byte {
	for index := width - 1; index >= 0; index-- {
		buffer[index] = byte(number%10) + '0'
		number /= 10
	}
	return buffer[:width]
}

func charsetPassword(number uint64, width int, charset string, buffer []byte) []byte {
	base := uint64(len(charset))
	for index := width - 1; index >= 0; index-- {
		buffer[index] = charset[number%base]
		number /= base
	}
	return buffer[:width]
}

func deflateProbe(
	state cryptoState, encrypted []byte, encryptedLimit, plainLimit int,
) bool {
	if encryptedLimit > len(encrypted) {
		encryptedLimit = len(encrypted)
	}
	compressed := make([]byte, encryptedLimit)
	for index, cipher := range encrypted[:encryptedLimit] {
		compressed[index] = state.decrypt(cipher)
	}
	reader := flate.NewReader(bytes.NewReader(compressed))
	defer reader.Close()
	plain := make([]byte, plainLimit)
	_, err := io.ReadFull(reader, plain)
	return err == nil
}

func passesProbe(password, encrypted []byte, checkByte byte) bool {
	state := newCryptoState(password)
	var last byte
	for _, cipher := range encrypted[:encryptionHeaderSize] {
		last = state.decrypt(cipher)
	}
	if last != checkByte {
		return false
	}

	payload := encrypted[encryptionHeaderSize:]
	if !deflateProbe(state, payload, quickEncryptedBytes, quickPlainBytes) {
		return false
	}
	return deflateProbe(state, payload, probeEncryptedBytes, probePlainBytes)
}

func verifyPassword(password []byte, target *encryptedTarget) bool {
	if !passesProbe(password, target.encrypted, target.checkByte) {
		return false
	}
	state := newCryptoState(password)
	for _, cipher := range target.encrypted[:encryptionHeaderSize] {
		state.decrypt(cipher)
	}
	compressed := make([]byte, len(target.encrypted)-encryptionHeaderSize)
	for index, cipher := range target.encrypted[encryptionHeaderSize:] {
		compressed[index] = state.decrypt(cipher)
	}
	reader := flate.NewReader(bytes.NewReader(compressed))
	defer reader.Close()
	hash := crc32.NewIEEE()
	written, err := io.Copy(hash, reader)
	return err == nil && uint64(written) == target.uncompressedSize && hash.Sum32() == target.crc
}

func encryptedProbe(path string) (*encryptedTarget, error) {
	reader, err := zip.OpenReader(path)
	if err != nil {
		return nil, err
	}
	defer reader.Close()

	var selected *zip.File
	for _, file := range reader.File {
		if file.FileInfo().IsDir() || file.Flags&1 == 0 {
			continue
		}
		if selected == nil || file.CompressedSize64 < selected.CompressedSize64 {
			selected = file
		}
	}
	if selected == nil {
		return nil, fmt.Errorf("ZIP 中没有加密文件")
	}
	if selected.Method != zip.Deflate {
		return nil, fmt.Errorf("仅支持 DEFLATE ZipCrypto，当前方法为 %d", selected.Method)
	}
	offset, err := selected.DataOffset()
	if err != nil {
		return nil, err
	}
	if selected.CompressedSize64 <= encryptionHeaderSize || selected.CompressedSize64 > uint64(^uint(0)>>1) {
		return nil, fmt.Errorf("加密成员大小不受支持：%d", selected.CompressedSize64)
	}

	handle, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer handle.Close()
	encrypted := make([]byte, int(selected.CompressedSize64))
	if _, err := handle.ReadAt(encrypted, offset); err != nil {
		return nil, err
	}
	checkByte := byte(selected.CRC32 >> 24)
	if selected.Flags&8 != 0 {
		checkByte = byte(selected.ModifiedTime >> 8)
	}
	return &encryptedTarget{
		encrypted: encrypted, checkByte: checkByte, crc: selected.CRC32,
		uncompressedSize: selected.UncompressedSize64,
	}, nil
}

func filterWordlist(
	path string, workers int, target *encryptedTarget, hits chan<- string, stopFirst bool,
	affixSpecials string, affixLength, maxLength int, insertSpecial bool,
) error {
	handle, err := os.Open(path)
	if err != nil {
		return err
	}
	defer handle.Close()

	jobs := make(chan string, workers*4)
	var found atomic.Bool
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			affix := make([]byte, affixLength)
			for password := range jobs {
				if found.Load() && stopFirst {
					continue
				}
				if affixSpecials == "" {
					if verifyPassword([]byte(password), target) {
						hits <- password
						if stopFirst {
							found.Store(true)
						}
					}
					continue
				}
				if len(password)+affixLength > maxLength {
					continue
				}
				if insertSpecial {
					candidate := make([]byte, len(password)+1)
					for position := 0; position <= len(password); position++ {
						copy(candidate, password[:position])
						copy(candidate[position+1:], password[position:])
						for index := range len(affixSpecials) {
							candidate[position] = affixSpecials[index]
							if verifyPassword(candidate, target) {
								hits <- string(candidate)
								if stopFirst {
									found.Store(true)
									return
								}
							}
						}
					}
					continue
				}
				affixCount, _ := powCount(uint64(len(affixSpecials)), uint64(affixLength))
				prefix := make([]byte, len(password)+affixLength)
				suffix := make([]byte, len(password)+affixLength)
				copy(prefix[affixLength:], password)
				copy(suffix, password)
				for index := range affixCount {
					decodeFixed(index, affixLength, affixSpecials, affix)
					copy(prefix, affix)
					copy(suffix[len(password):], affix)
					for _, candidate := range [][]byte{prefix, suffix} {
						if verifyPassword(candidate, target) {
							hits <- string(candidate)
							if stopFirst {
								found.Store(true)
								return
							}
						}
					}
				}
			}
		}()
	}

	scanner := bufio.NewScanner(handle)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	for scanner.Scan() {
		if found.Load() && stopFirst {
			break
		}
		password := scanner.Text()
		if password != "" {
			jobs <- password
		}
	}
	close(jobs)
	wait.Wait()
	return scanner.Err()
}

func filterNumeric(
	start, end uint64,
	width, workers int,
	target *encryptedTarget,
	hits chan<- string,
	stopFirst bool,
) {
	var next atomic.Uint64
	var found atomic.Bool
	next.Store(start)
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			buffer := make([]byte, width)
			for {
				if found.Load() && stopFirst {
					return
				}
				chunkStart := next.Add(workChunk) - workChunk
				if chunkStart > end {
					return
				}
				chunkEnd := chunkStart + workChunk - 1
				if chunkEnd > end {
					chunkEnd = end
				}
				for number := chunkStart; number <= chunkEnd; number++ {
					password := decimalPassword(number, width, buffer)
					if verifyPassword(password, target) {
						hits <- string(password)
						if stopFirst {
							found.Store(true)
							return
						}
					}
				}
			}
		}()
	}
	wait.Wait()
}

func filterCharsetLength(
	charset string,
	length, workers int,
	target *encryptedTarget,
	hits chan<- string,
	stopFirst bool,
) error {
	prefixLength := 2
	if length < prefixLength {
		prefixLength = length
	}
	prefixCount := 1
	var positions [256]int
	for index := range len(charset) {
		positions[charset[index]] = index
	}
	for range prefixLength {
		prefixCount *= len(charset)
	}
	jobs := make(chan []byte, workers*2)
	var found atomic.Bool
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			buffer := make([]byte, length)
			for prefix := range jobs {
				if found.Load() && stopFirst {
					return
				}
				copy(buffer, prefix)
				for index := prefixLength; index < length; index++ {
					buffer[index] = charset[0]
				}
				for {
					password := buffer[:length]
					if verifyPassword(password, target) {
						hits <- string(password)
						if stopFirst {
							found.Store(true)
							return
						}
					}
					index := length - 1
					for index >= prefixLength {
						position := positions[buffer[index]] + 1
						if position < len(charset) {
							buffer[index] = charset[position]
							break
						}
						buffer[index] = charset[0]
						index--
					}
					if index < prefixLength {
						break
					}
				}
			}
		}()
	}
	for number := range prefixCount {
		if found.Load() && stopFirst {
			break
		}
		prefix := make([]byte, prefixLength)
		charsetPassword(uint64(number), prefixLength, charset, prefix)
		jobs <- prefix
	}
	close(jobs)
	wait.Wait()
	return nil
}

func readTokens(path string, maxLength int) ([]string, error) {
	handle, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer handle.Close()
	seen := make(map[string]struct{})
	var tokens []string
	scanner := bufio.NewScanner(handle)
	for scanner.Scan() {
		token := strings.TrimSpace(scanner.Text())
		if token == "" || len(token) > maxLength {
			continue
		}
		isDigits := true
		for _, value := range []byte(token) {
			if value < '0' || value > '9' {
				isDigits = false
				break
			}
		}
		if !isDigits {
			return nil, fmt.Errorf("habit digit token contains a non-digit value: %q", token)
		}
		if _, exists := seen[token]; exists {
			continue
		}
		seen[token] = struct{}{}
		tokens = append(tokens, token)
	}
	return tokens, scanner.Err()
}

func powCount(base, exponent uint64) (uint64, error) {
	result := uint64(1)
	for range exponent {
		if base != 0 && result > ^uint64(0)/base {
			return 0, fmt.Errorf("candidate count exceeds uint64")
		}
		result *= base
	}
	return result, nil
}

func decodeFixed(number uint64, width int, charset string, destination []byte) {
	base := uint64(len(charset))
	for index := width - 1; index >= 0; index-- {
		destination[index] = charset[number%base]
		number /= base
	}
}

func habitPassword(
	index uint64, order string, alphaLength, specialLength int,
	alphaCharset, specialCharset string, digitTokens []string,
	buffer, alpha, special []byte,
) []byte {
	specialCount, _ := powCount(uint64(len(specialCharset)), uint64(specialLength))
	alphaCount, _ := powCount(uint64(len(alphaCharset)), uint64(alphaLength))
	specialIndex := index % specialCount
	index /= specialCount
	digitIndex := index % uint64(len(digitTokens))
	alphaIndex := (index / uint64(len(digitTokens))) % alphaCount
	decodeFixed(alphaIndex, alphaLength, alphaCharset, alpha)
	decodeFixed(specialIndex, specialLength, specialCharset, special)
	buffer = buffer[:0]
	for _, block := range order {
		switch block {
		case 'A':
			buffer = append(buffer, alpha...)
		case 'D':
			buffer = append(buffer, digitTokens[digitIndex]...)
		case 'S':
			buffer = append(buffer, special...)
		}
	}
	return buffer
}

func filterHabit(
	order string, alphaLength, specialLength, maxLength, workers int,
	alphaCharset, specialCharset string, digitTokens []string,
	target *encryptedTarget, hits chan<- string, stopFirst, requireMixedCase bool,
) error {
	if len(order) != 3 || !strings.Contains(order, "A") || !strings.Contains(order, "D") || !strings.Contains(order, "S") {
		return fmt.Errorf("habit order must be a permutation of ADS")
	}
	alphaCount, err := powCount(uint64(len(alphaCharset)), uint64(alphaLength))
	if err != nil {
		return err
	}
	specialCount, err := powCount(uint64(len(specialCharset)), uint64(specialLength))
	if err != nil {
		return err
	}
	if alphaCount > ^uint64(0)/uint64(len(digitTokens)) || alphaCount*uint64(len(digitTokens)) > ^uint64(0)/specialCount {
		return fmt.Errorf("habit candidate count exceeds uint64")
	}
	total := alphaCount * uint64(len(digitTokens)) * specialCount
	var next atomic.Uint64
	var found atomic.Bool
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			buffer := make([]byte, 0, maxLength)
			alpha := make([]byte, alphaLength)
			special := make([]byte, specialLength)
			for {
				if found.Load() && stopFirst {
					return
				}
				chunkStart := next.Add(workChunk) - workChunk
				if chunkStart >= total {
					return
				}
				chunkEnd := chunkStart + workChunk
				if chunkEnd > total {
					chunkEnd = total
				}
				for index := chunkStart; index < chunkEnd; index++ {
					password := habitPassword(
						index, order, alphaLength, specialLength,
						alphaCharset, specialCharset, digitTokens,
						buffer, alpha, special,
					)
					if len(password) > maxLength {
						continue
					}
					if requireMixedCase {
						hasLower, hasUpper := false, false
						for _, value := range alpha {
							hasLower = hasLower || value >= 'a' && value <= 'z'
							hasUpper = hasUpper || value >= 'A' && value <= 'Z'
						}
						if !hasLower || !hasUpper {
							continue
						}
					}
					if verifyPassword(password, target) {
						hits <- string(password)
						if stopFirst {
							found.Store(true)
							return
						}
					}
				}
			}
		}()
	}
	wait.Wait()
	return nil
}

func printableASCIICharset() string {
	charset := "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	for value := byte(33); value <= 126; value++ {
		if bytes.IndexByte([]byte(charset), value) < 0 {
			charset += string(value)
		}
	}
	return charset
}

func main() {
	zipPath := flag.String("zip", "", "encrypted ZIP path")
	wordlist := flag.String("wordlist", "", "UTF-8/ASCII password file, one per line")
	wordlistAffixSpecials := flag.String("wordlist-affix-specials", "", "prepend and append every special-character affix to wordlist entries")
	wordlistAffixLength := flag.Int("wordlist-affix-length", 1, "special-character affix length")
	wordlistInsertSpecial := flag.Bool("wordlist-insert-special", false, "insert one special character at every wordlist position")
	habitTokens := flag.String("habit-digit-tokens", "", "digit token file for habit templates")
	habitOrder := flag.String("habit-order", "ADS", "block order: a permutation of ADS")
	habitAlphaLength := flag.Int("habit-alpha-length", 2, "alphabetic block length")
	habitAlphaCharset := flag.String("habit-alpha-charset", "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "alphabetic characters for habit templates")
	habitRequireMixedCase := flag.Bool("habit-require-mixed-case", false, "require both lowercase and uppercase in the alphabetic block")
	habitSpecialLength := flag.Int("habit-special-length", 1, "special-character block length")
	habitSpecials := flag.String("habit-specials", "!@#$%&*_-?.", "special characters for habit templates")
	habitMaxLength := flag.Int("habit-max-length", 16, "maximum habit password length")
	charset := flag.String("charset", "", "brute-force byte characters")
	printable := flag.Bool("printable", false, "use all 94 non-space printable ASCII bytes")
	minLength := flag.Int("min-length", 1, "minimum charset password length")
	maxLength := flag.Int("max-length", 5, "maximum charset password length")
	start := flag.Uint64("start", 0, "inclusive numeric start")
	end := flag.Uint64("end", 999999, "inclusive numeric end")
	width := flag.Int("width", 6, "zero-padded password width")
	workers := flag.Int("workers", runtime.NumCPU(), "parallel workers")
	stopFirst := flag.Bool("stop-first", true, "stop after the first fully verified password")
	flag.Parse()
	if *printable {
		*charset = printableASCIICharset()
	}
	if *zipPath == "" || *end < *start || *width < 1 || *width > 19 || *workers < 1 ||
		*minLength < 1 || *maxLength < *minLength || *habitMaxLength < 1 ||
		*habitMaxLength > 16 || *habitAlphaLength < 1 || *habitAlphaLength > 3 ||
		*habitSpecialLength < 1 || *habitSpecialLength > 2 || *habitAlphaCharset == "" || *habitSpecials == "" ||
		*wordlistAffixLength < 1 || *wordlistAffixLength > 2 ||
		(*habitTokens != "" && *habitMaxLength <= *habitAlphaLength+*habitSpecialLength) {
		flag.Usage()
		os.Exit(2)
	}

	target, err := encryptedProbe(*zipPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}

	hits := make(chan string, *workers)
	runErrors := make(chan error, 1)
	go func() {
		var runErr error
		if *wordlist != "" {
			runErr = filterWordlist(
				*wordlist, *workers, target, hits, *stopFirst,
				*wordlistAffixSpecials, *wordlistAffixLength, *habitMaxLength, *wordlistInsertSpecial,
			)
		} else if *habitTokens != "" {
			tokens, tokenErr := readTokens(*habitTokens, *habitMaxLength-*habitAlphaLength-*habitSpecialLength)
			if tokenErr != nil {
				runErr = tokenErr
			} else if len(tokens) == 0 {
				runErr = fmt.Errorf("habit digit token file is empty")
			} else {
				runErr = filterHabit(
					*habitOrder, *habitAlphaLength, *habitSpecialLength, *habitMaxLength, *workers,
					*habitAlphaCharset, *habitSpecials, tokens,
					target, hits, *stopFirst, *habitRequireMixedCase,
				)
			}
		} else if *charset != "" {
			for length := *minLength; length <= *maxLength; length++ {
				runErr = filterCharsetLength(
					*charset, length, *workers, target, hits, *stopFirst,
				)
				if runErr != nil {
					break
				}
			}
		} else {
			filterNumeric(*start, *end, *width, *workers, target, hits, *stopFirst)
		}
		close(hits)
		runErrors <- runErr
	}()

	var candidates []string
	for candidate := range hits {
		candidates = append(candidates, candidate)
	}
	sort.Strings(candidates)
	for _, candidate := range candidates {
		fmt.Println(candidate)
	}
	if runErr := <-runErrors; runErr != nil {
		fmt.Fprintln(os.Stderr, runErr)
		os.Exit(2)
	}
}
